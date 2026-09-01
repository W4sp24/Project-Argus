"""What a generated note is *about*, and what it should link to.

A note Argus writes used to land in the vault as an island: one trailing
wikilink whose extension had been stripped (so for a PDF source it resolved to
nothing at all), no tags, no ``related``. Obsidian's graph, backlinks panel
and tag search could not reach generated content, which left the material
Argus produces findable only by full-text search.

This module is the pure half of fixing that. It takes a model's topic list and
a resolver's verdicts and produces an ordered, capped set of links plus the
frontmatter that goes with them. It performs **no I/O**: the resolver and the
neighbour list arrive as plain values, which is what lets the whole thing be
tested without a vault, an index or an embedding model.

Layering is the reason the retrieval half is not here.
:mod:`backend.rag.retrieve` already imports :mod:`backend.vault.links`, so this
module importing ``rag`` would be a cycle. Finding neighbours lives in
:mod:`backend.rag.neighbours`; composing the two lives in ``features/``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.vault.paths import is_indexable

#: How many concepts one note may claim. A model asked for "the concepts this
#: document is about" will happily name twenty, and twenty hollow links per
#: note is how a graph becomes noise rather than a map.
MAX_TOPICS = 7

#: How many existing vault notes are offered as neighbours.
MAX_NEIGHBOURS = 3

#: The section the prompt tail asks for, and the one parsed back off the body.
TOPICS_HEADING = "## Topics"

#: Everything between these two markers is Argus's to rewrite; everything
#: outside them is the user's. That is the whole basis of a safe relink -- a
#: hand-edited paragraph in a generated note survives, because the relink
#: replaces exactly this region and never looks at the rest.
FENCE_START = "<!-- argus:relations:start -->"
FENCE_END = "<!-- argus:relations:end -->"

#: Heading the fenced region carries. Inside the fence, so a relink replaces
#: it along with everything else and renaming it is a one-line change.
_SECTION_HEADING = "## Related"

_TOPICS_HEADING_RE = re.compile(rf"^{re.escape(TOPICS_HEADING)}[ \t]*$", re.MULTILINE)
_SECTION_RE = re.compile(
    rf"\n*{re.escape(FENCE_START)}.*?{re.escape(FENCE_END)}\n*",
    re.DOTALL,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_DECORATION_RE = re.compile(r"[*_`~]+")
# Case-sensitive, and deliberately so: "the will" is an article plus a
# concept, but "A Priori Knowledge" is a term whose first word happens to be
# spelled like one. A capitalised leading word is part of the name.
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")
_WORD_RE = re.compile(r"[A-Za-z][a-z']*")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TRAILING_PUNCT = " \t.,;:!?—–-"

#: Below this a "concept" is an initialism or a stray character, and linking it
#: produces a node nobody wants. Deliberately a length rule rather than a
#: stop-word list: a stop-word list is a maintenance burden that would still
#: miss whatever the next model says.
_MIN_TOPIC_CHARS = 3
_MAX_TOPIC_CHARS = 60


def normalise_topic(raw: str) -> str | None:
    """One model-written line as a concept name, or ``None`` if it is not one.

    Without this, one concept becomes three graph nodes -- ``determinism``,
    ``Determinism`` and ``**Determinism**`` are three distinct wikilink targets
    -- which makes the graph worse than no links at all.

    Title-casing runs **only** on input that is entirely lowercase, so
    ``RNA polymerase`` and ``pH balance`` keep the capitalisation the document
    gave them and only ``linear regression`` gets promoted. It capitalises
    across hyphens too, because ``self-possession`` is two words wearing one
    token and ``Self-possession`` reads as a typo.
    """
    text = _DECORATION_RE.sub("", raw or "")
    text = " ".join(text.split())
    text = text.strip(_TRAILING_PUNCT)
    text = _LEADING_ARTICLE_RE.sub("", text).strip(_TRAILING_PUNCT)
    if not (_MIN_TOPIC_CHARS <= len(text) <= _MAX_TOPIC_CHARS):
        return None
    if not any(char.isalpha() for char in text):
        return None
    if text.islower():
        text = _WORD_RE.sub(lambda match: match.group(0)[:1].upper() + match.group(0)[1:], text)
    return text


def parse_topics(body: str) -> tuple[str, list[str]]:
    """``(body without the Topics section, normalised topics)``.

    The prompt tail asks for a trailing ``## Topics`` section; this takes it
    back off, so the topics reach the reader as links rather than as a second
    list of bare names.

    Two robustness properties are load-bearing:

    * A model that ignores the instruction yields ``(body, [])`` and the note
      is written exactly as it is today. There is no failure path here at all
      -- that is what lets the feature ship without a fallback prompt, a retry
      or an error stage.
    * ``## Topics`` can legitimately appear *inside* a study guide's outline,
      so only the **last** occurrence is treated as the machine-readable one.
    """
    if not body:
        return body, []
    headings = list(_TOPICS_HEADING_RE.finditer(body))
    if not headings:
        return body, []
    heading = headings[-1]
    topics: list[str] = []
    seen: set[str] = set()
    for line in body[heading.end() :].splitlines():
        bullet = _BULLET_RE.match(line)
        if bullet is None:
            continue
        topic = normalise_topic(bullet.group("text"))
        if topic is None or topic.casefold() in seen:
            continue
        seen.add(topic.casefold())
        topics.append(topic)
        if len(topics) == MAX_TOPICS:
            break
    return body[: heading.start()].rstrip() + "\n", topics


def topic_tag(topic: str) -> str:
    """``Free Will`` -> ``topic/free-will``. Nested, so ``tag:#topic`` matches all."""
    return f"topic/{_SLUG_RE.sub('-', topic.casefold()).strip('-')}"


# --- links -------------------------------------------------------------------

LinkKind = Literal["concept", "neighbour", "source", "course"]

#: Name -> vault-relative path, or ``None``. In production this is a closure
#: over :meth:`backend.vault.links.LinkIndex.resolve` bound to the note's own
#: path; in tests it is a dict lookup.
Resolver = Callable[[str], str | None]


@dataclass(frozen=True)
class Relation:
    """One outbound link, and enough context to render and explain it."""

    target: str
    display: str | None
    kind: LinkKind
    resolved: bool

    def wikilink(self) -> str:
        """``[[target]]``, or ``[[target|display]]`` when the two differ."""
        if self.display is None or self.display == self.target:
            return f"[[{self.target}]]"
        return f"[[{self.target}|{self.display}]]"


@dataclass(frozen=True)
class Relations:
    """Everything a generated note should say about what it connects to."""

    topics: list[str]
    links: list[Relation]

    def of_kind(self, kind: LinkKind) -> list[Relation]:
        return [link for link in self.links if link.kind == kind]


def _strip_md(rel_path: str) -> str:
    """Wikilink target for a vault-relative path.

    Markdown loses its suffix -- Obsidian resolves ``[[folder/Note]]``, and
    keeping ``.md`` reads as a filename. Everything else keeps it, because
    ``[[essay]]`` for ``essay.pdf`` is precisely the bug this module fixes: it
    resolves to nothing, and renders as a link to a note that does not exist.
    """
    return rel_path[:-3] if rel_path.endswith(".md") else rel_path


def _folder_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def _stem_of(rel_path: str) -> str:
    name = rel_path.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name.rsplit(".", 1)[0]


def _concept(rel_path: str, *, display: str, from_path: str) -> Relation:
    """A resolved concept: qualified by path unless it sits in the same folder.

    Qualifying is not pedantry. ``build_link_index`` breaks a tie by shortest
    path, so a bare ``[[Overview]]`` written from one folder can silently point
    at another folder's ``Overview.md`` -- and this vault has five
    ``README.md`` files and thirteen ISLP chapters. Naming the file removes the
    ambiguity; the alias keeps the link reading as the concept.
    """
    if _folder_of(rel_path) == _folder_of(from_path):
        return Relation(target=_stem_of(rel_path), display=None, kind="concept", resolved=True)
    return Relation(target=_strip_md(rel_path), display=display, kind="concept", resolved=True)


def build_relations(
    *,
    topics: Sequence[str],
    resolve: Resolver,
    neighbours: Sequence[tuple[str, str]],
    source_rel_path: str,
    note_rel_path: str,
    course: str | None,
    taxonomy: Taxonomy | None = None,
) -> Relations:
    """Assemble one note's links: concepts, neighbours, source, course.

    ``neighbours`` is ``(vault_relative_path, title)`` already ranked by the
    caller -- :func:`backend.rag.neighbours.nearest_notes` in production, or a
    study guide's own cited corpus -- so this module never touches the index.

    Deduplication is by rendered target across *all* kinds: a concept that
    resolves to the same note a neighbour named appears once, not twice.
    """
    tax = taxonomy or active_taxonomy()
    links: list[Relation] = []
    claimed: set[str] = set()

    def claim(link: Relation) -> bool:
        if link.target in claimed:
            return False
        claimed.add(link.target)
        links.append(link)
        return True

    for topic in topics:
        hit = resolve(topic)
        if hit is None:
            claim(Relation(target=topic, display=None, kind="concept", resolved=False))
        else:
            claim(_concept(hit, display=topic, from_path=note_rel_path))

    excluded = {source_rel_path, note_rel_path}
    kept = 0
    for rel_path, title in neighbours:
        if kept == MAX_NEIGHBOURS or rel_path in excluded:
            continue
        # The same directory rule build_link_index applies, so a caller that
        # hands over a 99-Private/ or 90-Meta/ path gets it dropped here rather
        # than linked. Defence in depth for I3: the index cannot supply one in
        # the first place, and this is the layer that would have to be wrong
        # twice for a private title to reach a generated note.
        if not is_indexable(rel_path, taxonomy=tax):
            continue
        if claim(
            Relation(target=_strip_md(rel_path), display=title, kind="neighbour", resolved=True)
        ):
            kept += 1

    claim(
        Relation(
            target=_strip_md(source_rel_path),
            display=_stem_of(source_rel_path),
            kind="source",
            resolved=True,
        )
    )
    if course:
        claim(
            Relation(
                target=f"{tax.course_dir(course)}/course",
                display=course,
                kind="course",
                resolved=True,
            )
        )
    return Relations(topics=list(topics), links=links)


# --- rendering ---------------------------------------------------------------


def render_section(relations: Relations) -> str:
    """The fenced Related region, or ``""`` when there is nothing to link.

    Links go in the **body**, not only in frontmatter, because
    :data:`backend.rag.chunk.WIKILINK_RE` scans block text and not frontmatter
    -- so this section is what feeds ``retrieve.py``'s one-hop link expansion,
    and the feature improves Argus's own retrieval as well as Obsidian's UI.
    The frontmatter copy exists for Obsidian's Properties panel and for the
    vault's Concept Template convention; it is not the load-bearing one.
    """
    if not relations.links:
        return ""
    lines = [FENCE_START, "", _SECTION_HEADING, ""]

    concepts = relations.of_kind("concept")
    if concepts:
        lines += ["**Concepts** — " + " · ".join(link.wikilink() for link in concepts), ""]

    neighbours = relations.of_kind("neighbour")
    if neighbours:
        lines.append("**Also in your vault**")
        lines += [f"- {link.wikilink()}" for link in neighbours]
        lines.append("")

    for kind, label in (("source", "Source"), ("course", "Course")):
        for link in relations.of_kind(kind):
            lines.append(f"**{label}** — {link.wikilink()}")

    lines += ["", FENCE_END, ""]
    return "\n".join(lines)


def replace_section(body: str, section: str) -> str:
    """Splice ``section`` into ``body``, replacing any previous fenced region.

    Idempotent by construction, which is what makes ``argus relink`` safe to
    run twice: a second run produces byte-identical output. Everything outside
    the fence is the user's, so a hand-edited paragraph in a generated note
    survives -- this function never reads it, let alone rewrites it.
    """
    stripped = _SECTION_RE.sub("\n\n", body).rstrip()
    if not section:
        return stripped + "\n"
    return f"{stripped}\n\n{section.strip()}\n"


def strip_section(body: str) -> str:
    """``body`` with any fenced region removed -- the model's own words."""
    return _SECTION_RE.sub("\n\n", body).rstrip() + "\n"


def merge_frontmatter(
    front: dict,
    relations: Relations,
    *,
    kind: str,
    course: str | None,
) -> dict:
    """``front`` plus ``topics`` / ``tags`` / ``related``. Never deletes a key.

    Additive on purpose. The relink job runs this over notes a user may have
    edited by hand, and a backfill that dropped a key somebody added would be a
    data-loss bug wearing a feature's clothes. Tags the user added are kept,
    and Argus's own are de-duplicated into them rather than appended blindly --
    otherwise every relink would grow the list.
    """
    merged = dict(front)
    if relations.topics:
        merged["topics"] = list(relations.topics)

    existing = merged.get("tags") or []
    if isinstance(existing, str):
        existing = [existing]
    ours = [f"argus/{kind}"]
    if course:
        ours.append(f"course/{course}")
    ours += [topic_tag(topic) for topic in relations.topics]
    merged["tags"] = list(dict.fromkeys([*(str(tag) for tag in existing), *ours]))

    linked = [
        link.wikilink() for link in relations.links if link.kind in ("concept", "neighbour")
    ]
    if linked:
        merged["related"] = linked
    return merged
