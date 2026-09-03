# Note Relationships Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every note and study guide Argus writes emit real relationships — concepts, vault neighbours, source and course — so Obsidian's graph, backlinks panel and tag search can reach generated content.

**Architecture:** One pure module (`backend/vault/relations.py`) turns a model's topic list plus a resolver's verdicts into an ordered set of links, and renders them into a fenced region plus frontmatter. A thin retrieval helper (`backend/rag/neighbours.py`) supplies the semantic half. The ingest note writer and the study-guide writer both compose those two. A `relink` job on the existing `ingest_jobs` store backfills what was written before.

**Tech Stack:** Python 3.11+, FastAPI, `python-frontmatter`, ChromaDB + bge-small (via `backend.rag.index`), SQLite, pytest; Next.js 14 / TypeScript / Tailwind + SWR for the one frontend control; Playwright for e2e.

**Spec:** `docs/superpowers/specs/2026-09-01-notes-relationships-design.md`

## Global Constraints

- **Layering (README dependency rule).** `backend/vault/` and `backend/rag/` sit *below* `backend/core/` and `backend/features/`. `vault/relations.py` must not import from `rag/` or `features/` — `rag/retrieve.py` already imports `vault/links.py`, so the reverse is a cycle. `rag/neighbours.py` may import `vault/`. `features/` may import both.
- **Invariant I1 — one writer.** Every mutation of a user note goes through `backend/vault/writer.py`. New files under a course's `study/` are the one sanctioned exception and stay in `backend/features/study/`.
- **Invariant I2 — snapshot before write.** One `snapshot_vault()` per job, never one per file: `_git_snapshot` runs git with `check=False`, so two overlapping snapshots race on `.git/index.lock` and the loser fails silently.
- **Invariant I3 — private content never reaches a model or a link.** `build_link_index` and the RAG index both already exclude `99-Private/`, `90-Meta/` and `#no-ai`. No new code may widen that.
- **Taxonomy.** Never hardcode `15-Courses` / `60-Knowledge` / `00-Inbox`. Go through `Taxonomy` fields and derived properties. **Do not add a field to `Taxonomy`** — the spec rejects `Taxonomy.knowledge`, and `tests/core/test_taxonomy.py::test_defaults_match_v0_2_constants` pins the existing nine.
- **Caps (exact values):** `MAX_TOPICS = 7`, `MAX_NEIGHBOURS = 3`, `NEIGHBOUR_QUERY_CHARS = 1500`.
- **Fence markers (exact strings):** `<!-- argus:relations:start -->` and `<!-- argus:relations:end -->`.
- **Tag vocabulary (exact):** `argus/note` for an ingest note, `argus/guide` for a study guide, `course/<CODE>` verbatim from `course_of()`, `topic/<slug>` where slug is lowercase with non-alphanumerics collapsed to `-`.
- **`pytest` runs from `.venv`** or collection fails on this machine: `.venv/Scripts/python -m pytest -q`.
- **Every regression test must be run against the broken code first and observed to FAIL** before it is accepted. Record the failure message in the commit body or the task report.
- **Commit style:** imperative, lowercase, scoped — `feat(notes): …`, `fix(ingest): …`, `test(vault): …`. Body explains *why*. Footer:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MqyNDBXKBTg1oqGuJj6og3
  ```

---

## Wave 0 addendum — what actually landed

Wave 0 is **done and committed** (`6768c2c`, `9b3a65d`). Two interfaces moved
while it was built; the tasks below are written against these, not against the
originals:

1. **`build_relations` takes `taxonomy: Taxonomy | None = None`, not
   `courses_dir: str`.** The course link is built from
   `tax.course_dir(course)`, and the neighbour loop needs a taxonomy for its
   `is_indexable` check anyway, so threading one value beats threading two.
2. **The prompt tail is `backend.agent.formatting.topics_tail()`**, reading
   `backend/agent/prompts/topics.md` — not a constant in
   `features/ingest/notes.py`. It sits with `note_quality()` and
   `math_contract()` because the note styles and the study guide both need it,
   and prompt prose lives in `prompts/*.md` in this codebase. It is already
   written; compose it, do not re-author it.
3. **`GENERATED_BY` now lives in `backend/vault/sources.py`** and is
   re-exported from `features/ingest/notes.py`. Import it from `vault.sources`
   in any new module.

A third correction found by Wave 0's own tests, worth carrying: `normalise_topic`
strips only a **lowercase** leading article. "the will" is an article plus a
concept; "A Priori Knowledge" is a term whose first word is spelled like one.

## File Structure

| File | Responsibility |
|---|---|
| `backend/vault/sources.py` *(modify)* | Gains `GENERATED_BY = "argus"` beside the two suffix constants it already owns. One definition of "Argus wrote this". |
| `backend/vault/relations.py` *(create)* | Pure. Topic normalisation, topic parsing, link assembly, fenced rendering, frontmatter merge. No I/O, no `rag`, no `features`. |
| `backend/rag/neighbours.py` *(create)* | The retrieval half: nearest distinct notes for a block of text. Imports `retrieve`, returns plain tuples. |
| `backend/features/ingest/notes.py` *(modify)* | Prompt tail; `note_markdown` composes `relations`. |
| `backend/features/ingest/pipeline.py` *(modify)* | Builds one `LinkIndex` per job and threads it into `_write_note`. |
| `backend/features/study/study_guide.py` *(modify)* | Gains frontmatter it never had; neighbours come from the corpus it already cited. |
| `backend/features/notes/relink.py` *(create)* | The relink job body. Kept out of the router so the router stays a transport layer. |
| `backend/features/notes/router.py` *(modify)* | `POST /api/notes/relink`. |
| `backend/features/ingest/store.py` *(modify)* | `JOB_KINDS` gains `"relink"`; `SLOT_GROUPS` gains `"relink": "index"`. |
| `backend/vault/writer.py` *(modify)* | `update_note` gains `snapshot`/`log` kwargs, mirroring `create_note`. |
| `backend/cli.py` *(modify)* | `argus relink [--dry-run]`. |
| `web/lib/api.ts` *(modify)* | `relinkNotes()`. |
| `web/app/(dashboard)/sources/page.tsx` *(modify)* | `RELINK NOTES` action. |
| `tests/vault/test_relations.py` *(create)* | The pure module. |
| `tests/vault/test_relations_privacy.py` *(create)* | I3, its own file. |
| `tests/rag/test_neighbours.py` *(create)* | The retrieval half against a fake index. |
| `tests/features/ingest/test_notes_relations.py` *(create)* | Ingest note shape end to end. |
| `tests/features/study/test_guide_relations.py` *(create)* | Guide frontmatter + corpus neighbours. |
| `tests/features/notes/test_relink.py` *(create)* | Guard, idempotence, dry-run, re-upsert. |
| `web/e2e/relations.spec.ts` *(create)* | One spec. |
| `docs/notes-relationships.md` *(create)* | User-facing: what the Related section is, how to relink. |

---

## Task 1: Topic normalisation and parsing

**Files:**
- Create: `backend/vault/relations.py`
- Create: `tests/vault/test_relations.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MAX_TOPICS: int = 7`, `MAX_NEIGHBOURS: int = 3`
  - `TOPICS_HEADING: str = "## Topics"`
  - `normalise_topic(raw: str) -> str | None`
  - `parse_topics(body: str) -> tuple[str, list[str]]` — returns `(body_without_topics_section, topics)`
  - `topic_tag(topic: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/vault/test_relations.py
"""The pure half of note relationships: normalise, parse, build, render."""

from __future__ import annotations

import pytest

from backend.vault import relations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Determinism", "Determinism"),
        ("**Determinism**", "Determinism"),
        ("`self-possession`.", "Self-Possession"),
        ("linear regression", "Linear Regression"),
        ("  Free   Will  ", "Free Will"),
        ("the will", "Will"),
        ("A Priori Knowledge", "A Priori Knowledge"),
        ("_Bayes' Theorem_", "Bayes' Theorem"),
        ("Determinism:", "Determinism"),
    ],
)
def test_normalise_keeps_the_concept_and_drops_the_decoration(raw, expected):
    assert relations.normalise_topic(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "3", "42", "AI", "**", "- "])
def test_normalise_rejects_what_is_not_a_concept(raw):
    """Too short, purely numeric, or nothing but punctuation."""
    assert relations.normalise_topic(raw) is None


def test_normalise_does_not_recase_a_term_that_already_has_case():
    """Title-casing runs only on all-lowercase input, so 'RNA polymerase'
    and 'pH' keep the capitalisation the document gave them."""
    assert relations.normalise_topic("RNA polymerase") == "RNA polymerase"
    assert relations.normalise_topic("pH balance") == "pH balance"


def test_parse_topics_splits_the_section_off_the_body():
    body = (
        "An opening paragraph.\n\n"
        "## Key points\n\n"
        "- something\n\n"
        "## Topics\n\n"
        "- Determinism\n"
        "- **Free Will**\n"
    )
    remainder, topics = relations.parse_topics(body)
    assert topics == ["Determinism", "Free Will"]
    assert "## Topics" not in remainder
    assert remainder.rstrip().endswith("- something")


def test_parse_topics_returns_the_body_untouched_when_the_model_ignored_the_tail():
    """The whole feature has to degrade to today's behaviour, not fail."""
    body = "An opening paragraph.\n\n## Key points\n\n- something\n"
    remainder, topics = relations.parse_topics(body)
    assert topics == []
    assert remainder == body


def test_parse_topics_takes_the_last_section_when_a_style_names_one_earlier():
    """'## Topics' can legitimately appear inside a study guide's outline.
    Only the trailing section is the machine-readable one."""
    body = "## Topics\n\n- prose about topics\n\n## Notes\n\nreal content\n\n## Topics\n\n- Determinism\n"
    remainder, topics = relations.parse_topics(body)
    assert topics == ["Determinism"]
    assert "real content" in remainder
    assert remainder.count("## Topics") == 1


def test_parse_topics_caps_and_dedupes_case_insensitively():
    body = "body\n\n## Topics\n\n" + "\n".join(
        f"- {name}"
        for name in [
            "Determinism", "determinism", "Free Will", "Alpha", "Beta",
            "Gamma", "Delta", "Epsilon", "Zeta", "Eta",
        ]
    )
    _, topics = relations.parse_topics(body)
    assert topics[:3] == ["Determinism", "Free Will", "Alpha"]
    assert len(topics) == relations.MAX_TOPICS


@pytest.mark.parametrize(
    ("topic", "tag"),
    [
        ("Determinism", "topic/determinism"),
        ("Free Will", "topic/free-will"),
        ("Bayes' Theorem", "topic/bayes-theorem"),
        ("A/B Testing", "topic/a-b-testing"),
    ],
)
def test_topic_tag_is_a_stable_nested_slug(topic, tag):
    assert relations.topic_tag(topic) == tag
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/vault/test_relations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.vault.relations'`

- [ ] **Step 3: Write the module**

```python
# backend/vault/relations.py
"""What a generated note is *about*, and what it should link to.

A note Argus writes used to land in the vault as an island: one trailing
wikilink whose extension had been stripped (so for a PDF source it resolved
to nothing), no tags, no ``related``. Obsidian's graph, backlinks panel and
tag search could not reach generated content at all.

This module is the pure half of fixing that. It takes a model's topic list
and a resolver's verdicts and produces an ordered, capped set of links plus
the frontmatter that goes with them. It performs **no I/O**: the resolver and
the neighbour list arrive as plain values, which is what lets the whole thing
be tested without a vault or an embedding model.

Layering matters here. ``backend.rag.retrieve`` already imports
``backend.vault.links``, so this module must never import from ``rag`` or
``features`` — the retrieval half lives in :mod:`backend.rag.neighbours` and
the composition lives in ``features/``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

#: How many concepts one note may claim. A model asked for "the concepts this
#: document is about" will happily name twenty; twenty hollow links per note
#: is how a graph becomes noise rather than a map.
MAX_TOPICS = 7

#: How many existing vault notes are offered as neighbours.
MAX_NEIGHBOURS = 3

#: The section the prompt tail asks for, and the one parsed back off the body.
TOPICS_HEADING = "## Topics"

#: Everything between these two markers is Argus's to rewrite, and everything
#: outside them is the user's. That is the whole basis of a safe relink: a
#: hand-edit to a generated note's body survives, because the relink replaces
#: exactly this region and nothing else.
FENCE_START = "<!-- argus:relations:start -->"
FENCE_END = "<!-- argus:relations:end -->"

_TOPICS_SECTION_RE = re.compile(
    rf"^{re.escape(TOPICS_HEADING)}[ \t]*$(?P<items>.*)\Z",
    re.MULTILINE | re.DOTALL,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_DECORATION_RE = re.compile(r"[*_`~]+")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_TRAILING_PUNCT = " \t.,;:!?—–-"
_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: Below this, a "concept" is an initialism or a stray character, and linking
#: it produces a node nobody wants. Deliberately a length rule rather than a
#: stop-word list: a stop-word list is a maintenance burden that would still
#: miss the next thing.
_MIN_TOPIC_CHARS = 3
_MAX_TOPIC_CHARS = 60


def normalise_topic(raw: str) -> str | None:
    """One model-written line as a concept name, or ``None`` if it is not one.

    Without this, one concept becomes three graph nodes — ``determinism``,
    ``Determinism`` and ``**Determinism**`` are three distinct wikilink
    targets — which makes the graph worse than no links at all.

    Title-casing is applied **only** to input that is entirely lowercase, so
    ``RNA polymerase`` and ``pH balance`` keep the capitalisation the document
    gave them; only ``linear regression`` gets promoted.
    """
    text = _DECORATION_RE.sub("", raw or "")
    text = " ".join(text.split())
    text = text.strip(_TRAILING_PUNCT)
    text = _LEADING_ARTICLE_RE.sub("", text).strip()
    if len(text) < _MIN_TOPIC_CHARS or len(text) > _MAX_TOPIC_CHARS:
        return None
    if not any(char.isalpha() for char in text):
        return None
    if text.islower():
        text = " ".join(word[:1].upper() + word[1:] for word in text.split(" "))
    return text


def parse_topics(body: str) -> tuple[str, list[str]]:
    """``(body without the Topics section, normalised topics)``.

    The prompt tail asks for a trailing ``## Topics`` section; this takes it
    back off, so the topics reach the reader as links rather than as a second
    list of bare names.

    Two robustness properties are load-bearing:

    * A model that ignores the instruction yields ``(body, [])`` and the note
      is written exactly as it is today. There is no failure path here.
    * ``## Topics`` can legitimately appear *inside* a study guide's outline,
      so only the **last** occurrence is treated as the machine-readable one.
    """
    if not body:
        return body, []
    matches = list(_TOPICS_SECTION_RE.finditer(body))
    if not matches:
        return body, []
    match = matches[-1]
    topics: list[str] = []
    seen: set[str] = set()
    for line in match.group("items").splitlines():
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
    return body[: match.start()].rstrip() + "\n", topics


def topic_tag(topic: str) -> str:
    """``Free Will`` -> ``topic/free-will``. Nested so ``tag:#topic`` matches all."""
    return f"topic/{_SLUG_RE.sub('-', topic.casefold()).strip('-')}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/vault/test_relations.py -q`
Expected: PASS (all parametrised cases)

- [ ] **Step 5: Commit**

```bash
git add backend/vault/relations.py tests/vault/test_relations.py
git commit -m "feat(vault): a model's topic list, as concept names"
```

---

## Task 2: Link assembly

**Files:**
- Modify: `backend/vault/relations.py`
- Modify: `tests/vault/test_relations.py`

**Interfaces:**
- Consumes: Task 1's `MAX_TOPICS`, `MAX_NEIGHBOURS`, `normalise_topic`.
- Produces:
  - `Relation` — frozen dataclass: `target: str`, `display: str | None`, `kind: Literal["concept","neighbour","source","course"]`, `resolved: bool`
  - `Relations` — frozen dataclass: `topics: list[str]`, `links: list[Relation]`
  - `Resolver = Callable[[str], str | None]`
  - `build_relations(*, topics, resolve, neighbours, source_rel_path, note_rel_path, course, taxonomy=None) -> Relations`
  - `Relation.wikilink() -> str`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/vault/test_relations.py

def _resolver(mapping: dict[str, str]):
    """A LinkIndex.resolve stand-in: name -> vault-relative path, or None."""
    return lambda name: mapping.get(name)


def test_a_resolved_concept_is_path_qualified_with_an_alias():
    """Two READMEs and thirteen ISLP chapters share stems. A bare [[Name]]
    would cross-wire under the shortest-path ambiguity rule; qualifying it
    with an alias names the file and still reads as the concept."""
    built = relations.build_relations(
        topics=["Linear Regression"],
        resolve=_resolver({"Linear Regression": "60-Knowledge/AI/Linear Regression.md"}),
        neighbours=[],
        source_rel_path="15-Courses/CS101/materials/wk1.pdf",
        note_rel_path="15-Courses/CS101/notes/wk1.notes.md",
        course="CS101",
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is True
    assert concept.wikilink() == "[[60-Knowledge/AI/Linear Regression|Linear Regression]]"


def test_an_unresolved_concept_stays_bare_so_obsidian_can_create_it():
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is False
    assert concept.wikilink() == "[[Determinism]]"


def test_a_concept_in_the_notes_own_folder_is_not_qualified():
    """Same folder needs no path: it is unambiguous, and the shorter form is
    what a human would have typed."""
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({"Determinism": "00-Inbox/files/Determinism.md"}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.wikilink() == "[[Determinism]]"


def test_the_source_link_keeps_its_extension():
    """The live bug: [[essay]] for essay.pdf resolves to nothing in Obsidian.
    [[00-Inbox/files/essay.pdf|essay]] resolves to the attachment."""
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    source = next(link for link in built.links if link.kind == "source")
    assert source.wikilink() == "[[00-Inbox/files/essay.pdf|essay]]"


def test_a_markdown_source_links_without_the_suffix():
    """Obsidian resolves [[folder/Note]] for markdown; keeping '.md' would
    read as a filename rather than a note."""
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="50-Reference/paper.md",
        note_rel_path="50-Reference/paper.summary.md",
        course=None,
    )
    source = next(link for link in built.links if link.kind == "source")
    assert source.wikilink() == "[[50-Reference/paper|paper]]"


def test_the_course_link_is_present_inside_a_course_and_absent_outside_one():
    inside = relations.build_relations(
        topics=[], resolve=_resolver({}), neighbours=[],
        source_rel_path="15-Courses/ETHICS/materials/wk1.pdf",
        note_rel_path="15-Courses/ETHICS/notes/wk1.notes.md",
        course="ETHICS",
    )
    assert any(link.kind == "course" for link in inside.links)
    course = next(link for link in inside.links if link.kind == "course")
    assert course.wikilink() == "[[15-Courses/ETHICS/course|ETHICS]]"

    outside = relations.build_relations(
        topics=[], resolve=_resolver({}), neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    assert not any(link.kind == "course" for link in outside.links)


def test_neighbours_are_capped_and_never_include_the_note_or_its_source():
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[
            ("00-Inbox/files/essay.summary.md", "essay — summary"),   # itself
            ("00-Inbox/files/essay.pdf", "essay"),                     # its source
            ("50-Reference/a.md", "A"),
            ("50-Reference/b.md", "B"),
            ("50-Reference/c.md", "C"),
            ("50-Reference/d.md", "D"),
        ],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    found = [link for link in built.links if link.kind == "neighbour"]
    assert len(found) == relations.MAX_NEIGHBOURS
    assert [link.target for link in found] == [
        "50-Reference/a", "50-Reference/b", "50-Reference/c",
    ]


def test_a_concept_that_resolves_to_a_neighbour_is_not_linked_twice():
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({"Determinism": "50-Reference/Determinism.md"}),
        neighbours=[("50-Reference/Determinism.md", "Determinism")],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    targets = [link.target for link in built.links]
    assert targets.count("50-Reference/Determinism") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/vault/test_relations.py -q -k "resolved or source_link or course_link or neighbour or twice or markdown_source or own_folder"`
Expected: FAIL — `AttributeError: module 'backend.vault.relations' has no attribute 'build_relations'`

- [ ] **Step 3: Write the implementation**

Append to `backend/vault/relations.py`:

```python
LinkKind = Literal["concept", "neighbour", "source", "course"]

#: Name -> vault-relative path, or ``None``. In production this is a partial
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
        """``[[target]]`` or ``[[target|display]]``."""
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

    Markdown loses its suffix (Obsidian resolves ``[[folder/Note]]``, and
    keeping ``.md`` reads as a filename); everything else keeps it, because
    ``[[essay]]`` for ``essay.pdf`` is the bug this feature fixes — it
    resolves to nothing at all.
    """
    return rel_path[:-3] if rel_path.endswith(".md") else rel_path


def _folder_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def _stem_of(rel_path: str) -> str:
    name = rel_path.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name.rsplit(".", 1)[0]


def _qualified(rel_path: str, *, display: str, from_path: str) -> Relation:
    """A resolved link: qualified by path unless it sits in the same folder.

    Qualifying is not pedantry. ``build_link_index`` breaks a tie by shortest
    path, so a bare ``[[Overview]]`` written from one folder can silently
    point at another folder's ``Overview.md``. Naming the file removes the
    ambiguity; the alias keeps it reading as the concept.
    """
    target = _strip_md(rel_path)
    if _folder_of(rel_path) == _folder_of(from_path):
        return Relation(target=_stem_of(rel_path), display=None, kind="concept", resolved=True)
    return Relation(target=target, display=display, kind="concept", resolved=True)


def build_relations(
    *,
    topics: Sequence[str],
    resolve: Resolver,
    neighbours: Sequence[tuple[str, str]],
    source_rel_path: str,
    note_rel_path: str,
    course: str | None,
    courses_dir: str = "15-Courses",
) -> Relations:
    """Assemble one note's links: concepts, neighbours, source, course.

    ``neighbours`` is ``(vault_relative_path, title)`` already ranked by the
    caller — :func:`backend.rag.neighbours.nearest_notes` in production — so
    this module never touches the index.

    Deduplication is by rendered target across *all* kinds: a concept that
    resolves to the same note a neighbour named must appear once, not twice.
    """
    links: list[Relation] = []
    claimed: set[str] = set()

    def claim(link: Relation) -> None:
        if link.target in claimed:
            return
        claimed.add(link.target)
        links.append(link)

    for topic in topics:
        hit = resolve(topic)
        if hit is None:
            claim(Relation(target=topic, display=None, kind="concept", resolved=False))
        else:
            claim(_qualified(hit, display=topic, from_path=note_rel_path))

    excluded = {source_rel_path, note_rel_path}
    kept = 0
    for rel_path, title in neighbours:
        if rel_path in excluded or kept == MAX_NEIGHBOURS:
            continue
        before = len(links)
        claim(Relation(target=_strip_md(rel_path), display=title, kind="neighbour", resolved=True))
        if len(links) > before:
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
                target=f"{courses_dir}/{course}/course",
                display=course,
                kind="course",
                resolved=True,
            )
        )
    return Relations(topics=list(topics), links=links)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/vault/test_relations.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vault/relations.py tests/vault/test_relations.py
git commit -m "feat(vault): concepts, neighbours, source and course as one link set"
```

---

## Task 3: Fenced rendering and frontmatter merge

**Files:**
- Modify: `backend/vault/relations.py`
- Modify: `backend/vault/sources.py` — add `GENERATED_BY`
- Modify: `tests/vault/test_relations.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `render_section(relations: Relations) -> str` — the fenced block, `""` when there is nothing to say
  - `replace_section(body: str, section: str) -> str` — idempotent splice
  - `strip_section(body: str) -> str`
  - `merge_frontmatter(front: dict, relations: Relations, *, kind: str, course: str | None) -> dict`
  - `backend.vault.sources.GENERATED_BY: str = "argus"`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/vault/test_relations.py

def _sample() -> relations.Relations:
    return relations.build_relations(
        topics=["Determinism", "Free Will"],
        resolve=_resolver({"Determinism": "60-Knowledge/General/Determinism.md"}),
        neighbours=[("15-Courses/ETHICS/notes/sartre.notes.md", "Sartre")],
        source_rel_path="15-Courses/ETHICS/materials/wk1.pdf",
        note_rel_path="15-Courses/ETHICS/notes/wk1.notes.md",
        course="ETHICS",
    )


def test_the_section_is_fenced_so_a_relink_knows_what_is_its_own():
    section = relations.render_section(_sample())
    assert section.startswith(relations.FENCE_START)
    assert section.rstrip().endswith(relations.FENCE_END)
    assert "## Related" in section
    assert "[[Determinism]]" not in section  # resolved -> qualified
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in section
    assert "[[Free Will]]" in section        # unresolved -> bare
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in section


def test_nothing_to_say_renders_nothing():
    """A note with no topics, no neighbours and no course still has a source
    link, so this is the genuinely empty case: no links at all."""
    empty = relations.Relations(topics=[], links=[])
    assert relations.render_section(empty) == ""


def test_replacing_the_section_is_idempotent():
    body = "real content\n"
    once = relations.replace_section(body, relations.render_section(_sample()))
    twice = relations.replace_section(once, relations.render_section(_sample()))
    assert once == twice
    assert once.count(relations.FENCE_START) == 1


def test_a_hand_edit_outside_the_fence_survives_a_relink():
    body = "real content\n"
    once = relations.replace_section(body, relations.render_section(_sample()))
    edited = once.replace("real content", "real content\n\nEthan's own paragraph.")
    again = relations.replace_section(edited, relations.render_section(_sample()))
    assert "Ethan's own paragraph." in again
    assert again.count(relations.FENCE_START) == 1


def test_replacing_with_an_empty_section_removes_the_region_entirely():
    body = relations.replace_section("real content\n", relations.render_section(_sample()))
    assert relations.replace_section(body, "").rstrip() == "real content"


def test_strip_section_leaves_the_body_a_model_would_have_written():
    body = relations.replace_section("real content\n", relations.render_section(_sample()))
    assert relations.strip_section(body).rstrip() == "real content"


def test_merge_frontmatter_adds_its_keys_and_keeps_the_ones_it_did_not_add():
    front = {"title": "wk1 — summary", "type": "note", "prompt": "", "custom": "keep me"}
    merged = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    assert merged["custom"] == "keep me"
    assert merged["title"] == "wk1 — summary"
    assert merged["topics"] == ["Determinism", "Free Will"]
    assert "argus/note" in merged["tags"]
    assert "course/ETHICS" in merged["tags"]
    assert "topic/determinism" in merged["tags"]
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in merged["related"]


def test_merge_frontmatter_does_not_duplicate_tags_a_second_time_round():
    front = {"title": "t"}
    once = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    twice = relations.merge_frontmatter(dict(once), _sample(), kind="note", course="ETHICS")
    assert once["tags"] == twice["tags"]
    assert once["related"] == twice["related"]


def test_merge_frontmatter_keeps_a_users_own_tags():
    front = {"title": "t", "tags": ["reading/2026", "argus/note"]}
    merged = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    assert "reading/2026" in merged["tags"]
    assert merged["tags"].count("argus/note") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/vault/test_relations.py -q -k "section or frontmatter or fence or hand_edit"`
Expected: FAIL — `AttributeError: ... has no attribute 'render_section'`

- [ ] **Step 3a: Add `GENERATED_BY` to `backend/vault/sources.py`**

Immediately after the `SUMMARY_SUFFIX` definition:

```python
#: Written into every generated note's frontmatter. Lives here with the two
#: suffixes for the same reason they do: the ingest writer, the study guide,
#: the relink guard and the study guide's gap list all have to agree on one
#: string, and a second copy is how a backfill ends up rewriting a note a
#: human wrote.
GENERATED_BY = "argus"
```

Then in `backend/features/ingest/notes.py`, replace the local definition:

```python
# was: GENERATED_BY = "argus"
from backend.vault.sources import GENERATED_BY as _GENERATED_BY

GENERATED_BY = _GENERATED_BY
```

- [ ] **Step 3b: Write the rendering implementation**

Append to `backend/vault/relations.py`:

```python
_SECTION_RE = re.compile(
    rf"\n*{re.escape(FENCE_START)}.*?{re.escape(FENCE_END)}\n*",
    re.DOTALL,
)

#: Heading the fenced region carries. Inside the fence, so a relink replaces
#: it along with everything else and a rename is a one-line change.
_SECTION_HEADING = "## Related"


def render_section(relations_: Relations) -> str:
    """The fenced Related region, or ``""`` when there is nothing to link.

    Links go in the **body**, not only in frontmatter, because
    ``backend.rag.chunk.WIKILINK_RE`` scans block text and not frontmatter —
    so this section is what feeds ``retrieve.py``'s one-hop link expansion.
    The frontmatter copy exists for Obsidian's Properties panel and for the
    Concept Template's ``related:`` convention; it is not the load-bearing one.
    """
    if not relations_.links:
        return ""
    lines = [FENCE_START, "", _SECTION_HEADING, ""]

    concepts = relations_.of_kind("concept")
    if concepts:
        lines += ["**Concepts** — " + " · ".join(link.wikilink() for link in concepts), ""]

    neighbours = relations_.of_kind("neighbour")
    if neighbours:
        lines.append("**Also in your vault**")
        lines += [f"- {link.wikilink()}" for link in neighbours]
        lines.append("")

    for kind, label in (("source", "Source"), ("course", "Course")):
        for link in relations_.of_kind(kind):
            lines.append(f"**{label}** — {link.wikilink()}")

    lines += ["", FENCE_END, ""]
    return "\n".join(lines)


def replace_section(body: str, section: str) -> str:
    """Splice ``section`` into ``body``, replacing any previous fenced region.

    Idempotent by construction, which is what makes ``argus relink`` safe to
    run twice. Everything outside the fence is the user's: a hand-edited
    paragraph in a generated note survives a relink because this function
    never looks at it.
    """
    stripped = _SECTION_RE.sub("\n\n", body).rstrip()
    if not section:
        return stripped + "\n"
    return f"{stripped}\n\n{section.strip()}\n"


def strip_section(body: str) -> str:
    """``body`` with any fenced region removed — the model's own words."""
    return _SECTION_RE.sub("\n\n", body).rstrip() + "\n"


def merge_frontmatter(
    front: dict,
    relations_: Relations,
    *,
    kind: str,
    course: str | None,
) -> dict:
    """``front`` plus ``topics``/``tags``/``related``. Never deletes a key.

    Additive on purpose: the relink job runs this over notes a user may have
    edited, and a backfill that dropped a key somebody added by hand would be
    a data-loss bug wearing a feature's clothes. Tags a user added are kept
    and Argus's own are de-duplicated into them.
    """
    merged = dict(front)
    if relations_.topics:
        merged["topics"] = list(relations_.topics)

    existing = merged.get("tags") or []
    if isinstance(existing, str):
        existing = [existing]
    ours = [f"argus/{kind}"]
    if course:
        ours.append(f"course/{course}")
    ours += [topic_tag(topic) for topic in relations_.topics]
    merged["tags"] = list(dict.fromkeys([*(str(tag) for tag in existing), *ours]))

    linked = [link.wikilink() for link in relations_.links if link.kind in ("concept", "neighbour")]
    if linked:
        merged["related"] = linked
    return merged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/vault/ -q && .venv/Scripts/python -m ruff check backend/vault/relations.py`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add backend/vault/relations.py backend/vault/sources.py backend/features/ingest/notes.py tests/vault/test_relations.py
git commit -m "feat(vault): a fenced Related region a relink can rewrite safely"
```

---

## Task 4: Neighbours from the index, and the privacy boundary

**Files:**
- Create: `backend/rag/neighbours.py`
- Create: `tests/rag/test_neighbours.py`
- Create: `tests/vault/test_relations_privacy.py`

**Interfaces:**
- Consumes: `backend.rag.retrieve.retrieve_result`, `backend.vault.links.build_link_index`.
- Produces: `nearest_notes(index, vault_path, text, *, exclude, limit=MAX_NEIGHBOURS, taxonomy=None) -> list[tuple[str, str]]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/rag/test_neighbours.py
"""The retrieval half of note relationships."""

from __future__ import annotations

from pathlib import Path

from backend.rag import neighbours


class _FakeIndex:
    """Stands in for VaultIndex. `retrieve_result` is monkeypatched, so this
    only has to be an object identity the call is threaded through."""


def test_nearest_notes_dedupes_by_path_and_keeps_rank_order(monkeypatch, tmp_path):
    hits = [
        {"meta": {"path": "50-Reference/a.md", "title": "A"}, "text": "", "score": 0.9},
        {"meta": {"path": "50-Reference/a.md", "title": "A"}, "text": "", "score": 0.8},
        {"meta": {"path": "50-Reference/b.md", "title": "B"}, "text": "", "score": 0.7},
    ]
    monkeypatch.setattr(
        neighbours, "retrieve_result",
        lambda *a, **k: type("R", (), {"results": hits, "related": []})(),
    )
    found = neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set())
    assert found == [("50-Reference/a.md", "A"), ("50-Reference/b.md", "B")]


def test_nearest_notes_honours_exclude_and_limit(monkeypatch, tmp_path):
    hits = [
        {"meta": {"path": f"50-Reference/{name}.md", "title": name}, "text": "", "score": 0.9}
        for name in ("self", "a", "b", "c", "d")
    ]
    monkeypatch.setattr(
        neighbours, "retrieve_result",
        lambda *a, **k: type("R", (), {"results": hits, "related": []})(),
    )
    found = neighbours.nearest_notes(
        _FakeIndex(), tmp_path, "text", exclude={"50-Reference/self.md"}, limit=2
    )
    assert [path for path, _ in found] == ["50-Reference/a.md", "50-Reference/b.md"]


def test_nearest_notes_asks_for_first_order_neighbours_only(monkeypatch, tmp_path):
    """expand_links=True would return neighbours-of-neighbours, which are not
    what 'also in your vault' means."""
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return type("R", (), {"results": [], "related": []})()

    monkeypatch.setattr(neighbours, "retrieve_result", _capture)
    neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set())
    assert seen["expand_links"] is False


def test_a_dead_index_yields_no_neighbours_rather_than_failing_the_note(monkeypatch, tmp_path):
    """A note whose neighbours could not be computed is still a good note.
    Losing the whole note because chroma is unavailable is not a trade worth
    making."""
    def _boom(*args, **kwargs):
        raise RuntimeError("chroma is unavailable")

    monkeypatch.setattr(neighbours, "retrieve_result", _boom)
    assert neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set()) == []
```

```python
# tests/vault/test_relations_privacy.py
"""Invariant I3 for note relationships, in its own file.

I3 says private content never reaches a model — and, once notes carry links,
never reaches a *link* either. A wikilink into 99-Private/ would put a private
note's title into a note that gets indexed, retrieved and cited.

Both halves are inherited rather than reimplemented: build_link_index skips
anything is_indexable() refuses, and the RAG index never held those chunks in
the first place. These tests exist because "inherited" is a claim, and this is
the one place in the feature where being wrong is a privacy breach rather than
a cosmetic defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.vault import relations
from backend.vault.links import build_link_index


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "99-Private").mkdir()
    (tmp_path / "99-Private" / "Therapy Notes.md").write_text(
        "---\ntitle: Therapy Notes\naliases: [Determinism]\n---\n\nprivate\n",
        encoding="utf-8",
    )
    (tmp_path / "90-Meta").mkdir()
    (tmp_path / "90-Meta" / "Determinism.md").write_text("journal\n", encoding="utf-8")
    (tmp_path / "50-Reference").mkdir()
    (tmp_path / "50-Reference" / "Public.md").write_text("public\n", encoding="utf-8")
    return tmp_path


def test_a_private_note_is_not_a_resolvable_concept(vault: Path):
    index = build_link_index(vault)
    built = relations.build_relations(
        topics=["Therapy Notes"],
        resolve=lambda name: index.resolve(name, from_path="50-Reference/x.summary.md"),
        neighbours=[],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is False
    assert "99-Private" not in relations.render_section(built)


def test_a_private_alias_cannot_capture_a_public_concept_name(vault: Path):
    """The private note claims the alias 'Determinism'. A hollow link is the
    correct outcome; a link into 99-Private/ would be the breach."""
    index = build_link_index(vault)
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=lambda name: index.resolve(name, from_path="50-Reference/x.summary.md"),
        neighbours=[],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.wikilink() == "[[Determinism]]"
    assert "99-Private" not in relations.render_section(built)
    assert "90-Meta" not in relations.render_section(built)


def test_a_private_path_offered_as_a_neighbour_is_still_refused(vault: Path):
    """Defence in depth: the index cannot supply one, but if a caller ever
    hands one over, it must not become a link."""
    built = relations.build_relations(
        topics=[],
        resolve=lambda name: None,
        neighbours=[("99-Private/Therapy Notes.md", "Therapy Notes")],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    assert not [link for link in built.links if link.kind == "neighbour"]
    assert "99-Private" not in relations.render_section(built)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/rag/test_neighbours.py tests/vault/test_relations_privacy.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.rag.neighbours`, and `test_a_private_path_offered_as_a_neighbour_is_still_refused` fails because `build_relations` does not yet filter private paths.

- [ ] **Step 3a: Write `backend/rag/neighbours.py`**

```python
"""Which existing notes a new note is nearest to.

Split from :mod:`backend.vault.relations` for a layering reason, not a
stylistic one: ``retrieve`` imports ``vault.links``, so ``vault`` importing
``retrieve`` would be a cycle. The pure half stays in ``vault/``; this is the
half that needs the index.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy
from backend.rag.retrieve import retrieve_result
from backend.vault.relations import MAX_NEIGHBOURS

logger = logging.getLogger("argus.rag")

#: How much of a note is used as the neighbour query. The whole note would
#: blur the query toward the note's average subject; the opening is where a
#: note says what it is about.
NEIGHBOUR_QUERY_CHARS = 1500


def nearest_notes(
    index: Any,
    vault_path: Path,
    text: str,
    *,
    exclude: Iterable[str],
    limit: int = MAX_NEIGHBOURS,
    taxonomy: Taxonomy | None = None,
) -> list[tuple[str, str]]:
    """``(rel_path, title)`` for the nearest distinct notes, best first.

    ``expand_links=False`` because the question is "what else in the vault is
    about this", not "what do those things link to" — one hop out is already
    what the reader gets from the links themselves.

    Never raises. A note whose neighbours could not be computed is still a
    good note; losing it because chroma is unavailable is not a trade worth
    making, and the same reasoning already governs
    ``pipeline._run_one``'s treatment of a dead generator.
    """
    body = (text or "").strip()[:NEIGHBOUR_QUERY_CHARS]
    if not body:
        return []
    skip = set(exclude)
    try:
        result = retrieve_result(
            index,
            body,
            vault_path,
            k=8,
            expand_links=False,
            taxonomy=taxonomy,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("neighbours unavailable: %s", exc)
        return []

    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hit in result.results:
        meta = hit.get("meta") or {}
        rel_path = str(meta.get("path") or "")
        if not rel_path or rel_path in skip or rel_path in seen:
            continue
        seen.add(rel_path)
        title = str(meta.get("title") or rel_path.rsplit("/", 1)[-1])
        found.append((rel_path, title))
        if len(found) == limit:
            break
    return found
```

- [ ] **Step 3b: Add the private-path guard to `build_relations`**

In `backend/vault/relations.py`, add the import and the check:

```python
from backend.vault.paths import is_indexable
```

and inside `build_relations`, replace the neighbour loop's guard:

```python
    for rel_path, title in neighbours:
        # is_indexable is the same directory rule build_link_index applies, so
        # a caller that hands over a 99-Private/ or 90-Meta/ path gets it
        # dropped here rather than linked (I3, defence in depth — the index
        # cannot supply one in the first place).
        if rel_path in excluded or kept == MAX_NEIGHBOURS or not is_indexable(rel_path):
            continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/rag/test_neighbours.py tests/vault/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/rag/neighbours.py backend/vault/relations.py tests/rag/test_neighbours.py tests/vault/test_relations_privacy.py
git commit -m "feat(rag): nearest vault notes, and the I3 boundary around them"
```

---

## Task 5: Ingest notes emit relationships

**Files:**
- Modify: `backend/features/ingest/notes.py`
- Modify: `backend/features/ingest/pipeline.py:145-205` (`_write_note`) and `:245-290` (`_run`)
- Create: `tests/features/ingest/test_notes_relations.py`

**Interfaces:**
- Consumes: `relations.parse_topics`, `relations.build_relations`, `relations.render_section`, `relations.replace_section`, `relations.merge_frontmatter`, `neighbours.nearest_notes`, `links.build_link_index`.
- Produces:
  - `notes.note_markdown(rel_path, style, instruction, body, *, taxonomy=None, resolve=None, neighbours=()) -> tuple[str, str]` — the two new keyword-only params default to "no relationships", so every existing caller and test keeps working.

- [ ] **Step 1: Write the failing test**

```python
# tests/features/ingest/test_notes_relations.py
"""What an ingested file's note says about the rest of the vault."""

from __future__ import annotations

import frontmatter

from backend.features.ingest import notes as note_styles


def _resolver(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


BODY = (
    "Kavanaugh contrasts Skinner and Sartre.\n\n"
    "## Key points\n\n- a point\n\n"
    "## Topics\n\n- Determinism\n- **Free Will**\n"
)


def test_the_topics_section_becomes_links_not_a_second_list():
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        BODY,
        resolve=_resolver({"Determinism": "60-Knowledge/General/Determinism.md"}),
        neighbours=[("15-Courses/ETHICS/notes/sartre.notes.md", "Sartre")],
    )
    post = frontmatter.loads(markdown)
    assert "## Topics" not in post.content
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in post.content
    assert "[[Free Will]]" in post.content
    assert "[[15-Courses/ETHICS/notes/sartre.notes|Sartre]]" in post.content


def test_the_source_link_resolves_to_the_pdf_rather_than_to_nothing():
    """The pre-existing bug: [[wk1]] for wk1.pdf is a hollow node."""
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"], "", BODY,
    )
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in markdown
    assert "\n[[wk1]]\n" not in markdown


def test_frontmatter_carries_topics_tags_and_related():
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"], "", BODY,
        resolve=_resolver({}),
    )
    post = frontmatter.loads(markdown)
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "argus/note" in post["tags"]
    assert "course/ETHICS" in post["tags"]
    assert "topic/free-will" in post["tags"]
    assert post["generated_by"] == "argus"


def test_a_model_that_ignores_the_tail_still_produces_todays_note():
    """The degradation path. No topics, no crash, still a source and a course."""
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"], "",
        "Just a summary with no topics section.\n",
    )
    post = frontmatter.loads(markdown)
    assert "topics" not in post.metadata
    assert "Just a summary" in post.content
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in post.content


def test_the_prompt_asks_for_the_topics_section():
    prompt = note_styles.build_prompt(
        note_styles.NOTE_STYLES["cornell"], "", "a/b.pdf", "text"
    )
    assert "## Topics" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/features/ingest/test_notes_relations.py -q`
Expected: FAIL — `TypeError: note_markdown() got an unexpected keyword argument 'resolve'`

- [ ] **Step 3a: Add the prompt tail in `backend/features/ingest/notes.py`**

After `_HOUSE_RULES`:

```python
#: Appended to every style's prompt and to the course-wide study guide's.
#: Parsed back off the body by ``relations.parse_topics``, so the reader sees
#: links rather than a second list of bare names.
#:
#: Worth stating plainly because it is the property the whole feature rests
#: on: a model that ignores this section costs nothing. ``parse_topics``
#: returns no topics, the note is written exactly as it is today, and only the
#: concept links are missing. There is no failure path.
TOPICS_TAIL = """Finally, after everything above, add one more section:

## Topics

Three to seven concept names, one per line, each prefixed with `- `. A concept
name is the thing itself as a textbook would title it — "Determinism",
"Linear Regression", "Bayes' Theorem" — not a sentence, not a phrase copied
out of this document, and not the document's own title."""
```

and include it in `build_prompt`:

```python
    return _PROMPT.format(
        instruction=compose(style.instruction if style else "", instruction),
        house_rules=compose(_HOUSE_RULES, note_quality(), math_contract(), TOPICS_TAIL),
        path=rel_path,
        text=text[:MAX_NOTE_CHARS],
    )
```

- [ ] **Step 3b: Rewrite `note_markdown`**

```python
def note_markdown(
    rel_path: str,
    style: NoteStyle | None,
    instruction: str,
    body: str,
    *,
    taxonomy: Taxonomy | None = None,
    resolve: relations.Resolver | None = None,
    neighbours: Sequence[tuple[str, str]] = (),
) -> tuple[str, str]:
    """``(note_rel_path, markdown)`` for one source file.

    ``resolve`` and ``neighbours`` are how the note learns about the rest of
    the vault. Both default to "nothing known", so a caller that has no link
    index and no live chroma collection still gets a correct note — with a
    source link, a course link and whatever concepts the model named, rendered
    hollow. That default is what keeps every pre-existing caller and test
    working unedited.

    The trailing links are load-bearing rather than decorative: they are what
    lets ``retrieve.py``'s one-hop link expansion reach the source and the
    neighbours from the note, with no new retrieval code.
    """
    tax = taxonomy or active_taxonomy()
    source = PurePosixPath(rel_path)
    destination = note_destination(rel_path, taxonomy=tax)
    prose, topics = relations.parse_topics(body.strip())
    code = course_of(rel_path, taxonomy=tax)
    built = relations.build_relations(
        topics=topics,
        resolve=resolve or (lambda _name: None),
        neighbours=neighbours,
        source_rel_path=rel_path,
        note_rel_path=destination,
        course=code,
        courses_dir=tax.courses,
    )
    front: dict = {
        "title": f"{source.stem} — {style.label.lower() if style else 'summary'}",
        "type": "note",
        "generated_by": GENERATED_BY,
        "note_style": style.key if style else "custom",
        "source": rel_path,
        "prompt": instruction,
    }
    if code is not None:
        # Chunk metadata already carries `course` derived from the path, but
        # the frontmatter is what a human -- and Obsidian's own search -- reads.
        front["course"] = code
    front = relations.merge_frontmatter(front, built, kind="note", course=code)
    content = relations.replace_section(prose, relations.render_section(built))
    return destination, frontmatter.dumps(frontmatter.Post(content, **front)) + "\n"
```

Add the imports at the top of the module:

```python
from collections.abc import Sequence

from backend.vault import relations
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/features/ingest/ -q`
Expected: PASS — including the pre-existing ingest tests, which must not regress.

- [ ] **Step 5: Commit**

```bash
git add backend/features/ingest/notes.py tests/features/ingest/test_notes_relations.py
git commit -m "feat(ingest): a note says what it is about, and what that connects to"
```

- [ ] **Step 6: Thread one link index per job through the pipeline**

In `backend/features/ingest/pipeline.py`, `_run()` builds the index once, beside the `VaultIndex` construction:

```python
    index = index_factory()
    # One vault walk for the whole job, for the same reason the VaultIndex is
    # constructed once: build_link_index rglobs the vault and parses every
    # note's frontmatter, so building it per file would be N vault walks for
    # one ingest. Failure is not fatal -- a note with hollow concept links is
    # a worse note, not a lost one.
    try:
        link_index = build_link_index(settings.vault_path, taxonomy=settings.taxonomy)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest: link index unavailable, concepts will not resolve: %s", exc)
        link_index = None
    snapshot_vault(settings.vault_path, f"ingest {len(job['items'])} file(s) into {target}")
```

Pass `link_index=link_index` down through `_run_one(...)` into `_write_note(...)`, and in `_write_note`, immediately before the `note_markdown` call:

```python
    destination = note_styles.note_destination(rel_path, taxonomy=settings.taxonomy)
    resolve = (
        (lambda name: link_index.resolve(name, from_path=destination))
        if link_index is not None
        else None
    )
    near = nearest_notes(
        index,
        settings.vault_path,
        body,
        exclude={rel_path, destination},
        taxonomy=settings.taxonomy,
    )
    note_path, markdown = note_styles.note_markdown(
        rel_path, style, instruction, body,
        taxonomy=settings.taxonomy, resolve=resolve, neighbours=near,
    )
```

with the imports:

```python
from backend.rag.neighbours import nearest_notes
from backend.vault.links import build_link_index
```

- [ ] **Step 7: Run the whole ingest suite**

Run: `.venv/Scripts/python -m pytest tests/features/ingest/ tests/vault/ tests/rag/ -q && .venv/Scripts/python -m ruff check backend/`
Expected: PASS, ruff clean

- [ ] **Step 8: Commit**

```bash
git add backend/features/ingest/pipeline.py
git commit -m "feat(ingest): one link index per job, not one per file"
```

---

## Task 6: Study guides gain frontmatter and relationships

**Files:**
- Modify: `backend/features/study/study_guide.py:88-160`
- Create: `tests/features/study/test_guide_relations.py`

**Interfaces:**
- Consumes: Tasks 1–4, plus `backend.agent.formatting.topics_tail()`.
- Produces: `guide_markdown(course, scope, body, corpus, *, resolve=None, courses_dir="15-Courses") -> str` — exported so the test can exercise it without a generator.

- [ ] **Step 1: Write the failing test**

```python
# tests/features/study/test_guide_relations.py
"""A study guide is the same kind of artifact as the note beside it."""

from __future__ import annotations

import frontmatter

from backend.features.study import study_guide


CORPUS = [
    {"text": "chunk one", "meta": {"path": "15-Courses/ETHICS/materials/wk1.pdf", "title": "wk1", "page": 3}},
    {"text": "chunk two", "meta": {"path": "15-Courses/ETHICS/materials/wk2.pdf", "title": "wk2", "page": 1}},
    {"text": "chunk three", "meta": {"path": "15-Courses/ETHICS/materials/wk1.pdf", "title": "wk1", "page": 9}},
]

BODY = "## Outline\n\n- a topic\n\n## Topics\n\n- Determinism\n- Free Will\n"


def test_a_guide_now_carries_the_frontmatter_it_never_had():
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)
    post = frontmatter.loads(markdown)
    assert post["type"] == "guide"
    assert post["course"] == "ETHICS"
    assert post["scope"] == "midterm"
    assert post["generated_by"] == "argus"
    assert post["title"] == "ETHICS — midterm"
    assert "argus/guide" in post["tags"]
    assert "course/ETHICS" in post["tags"]


def test_the_cited_materials_become_the_guides_neighbours_deduped():
    """A guide already holds the chunks it cited. Those are better neighbours
    than a fresh similarity query, and need no index."""
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in markdown
    assert markdown.count("materials/wk1.pdf") == 1
    assert "[[15-Courses/ETHICS/materials/wk2.pdf|wk2]]" in markdown


def test_the_topics_section_is_lifted_off_the_guide_body_too():
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)
    post = frontmatter.loads(markdown)
    assert "## Topics" not in post.content
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "[[Determinism]]" in post.content


def test_a_guide_with_no_corpus_and_no_topics_is_still_a_valid_guide():
    markdown = study_guide.guide_markdown("ETHICS", "midterm", "## Outline\n\n- a\n", [])
    post = frontmatter.loads(markdown)
    assert post["course"] == "ETHICS"
    assert "## Outline" in post.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/features/study/test_guide_relations.py -q`
Expected: FAIL — `AttributeError: module 'backend.features.study.study_guide' has no attribute 'guide_markdown'`

- [ ] **Step 3: Implement**

In `backend/features/study/study_guide.py`, add to `guide_prompt`'s composition:

```python
    from backend.features.ingest.notes import TOPICS_TAIL

    return compose(
        structure, note_quality(), math_contract(), TOPICS_TAIL,
        f"SOURCES:\n{''.join(excerpts)}",
    )
```

Add the new function:

```python
def guide_markdown(
    course: str,
    scope: str,
    body: str,
    corpus: list[dict[str, Any]],
    *,
    resolve: relations.Resolver | None = None,
    courses_dir: str = "15-Courses",
) -> str:
    """One guide as a complete note: frontmatter, prose, Related region.

    Guides used to be written as ``body + "\\n"`` — no title, no type, no
    course, no links — which made them invisible to every Obsidian query axis
    except full-text search. They are the same kind of artifact as the note
    sitting beside them in the vault, so they render through the same module.

    A guide needs **no index**: it already holds the corpus chunks it cited,
    and the materials it actually drew from are better neighbours than a fresh
    similarity query would produce.
    """
    prose, topics = relations.parse_topics(body.strip())
    cited: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in corpus:
        meta = chunk.get("meta") or {}
        path = str(meta.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        cited.append((path, str(meta.get("title") or path.rsplit("/", 1)[-1])))

    built = relations.build_relations(
        topics=topics,
        resolve=resolve or (lambda _name: None),
        neighbours=cited,
        # A guide has no single source file, and `note_rel_path` is only used
        # to decide same-folder qualification. Naming the study folder gives
        # both the right answer without inventing a file that does not exist.
        source_rel_path=f"{courses_dir}/{course}/study",
        note_rel_path=f"{courses_dir}/{course}/study/guide.md",
        course=course,
        courses_dir=courses_dir,
    )
    # The synthetic source link above is an artefact of the shared builder,
    # not something a reader should see: a guide's sources are its neighbours.
    built = relations.Relations(
        topics=built.topics,
        links=[link for link in built.links if link.kind != "source"],
    )
    front: dict = {
        "title": f"{course} — {scope}",
        "type": "guide",
        "generated_by": GENERATED_BY,
        "course": course,
        "scope": scope,
    }
    front = relations.merge_frontmatter(front, built, kind="guide", course=course)
    content = relations.replace_section(prose, relations.render_section(built))
    return frontmatter.dumps(frontmatter.Post(content, **front)) + "\n"
```

with imports:

```python
import frontmatter

from backend.vault import relations
from backend.vault.sources import GENERATED_BY
```

Then in `generate_study_guide`, replace the write:

```python
    markdown = guide_markdown(
        course, scope, body, corpus, courses_dir=tax.courses,
    )
    ...
    (study_dir / name).write_text(markdown, encoding="utf-8")
```

Keep the existing gap-list append, but apply it to `body` **before** `guide_markdown` runs, so the checklist lands inside the prose rather than after the fenced region.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/features/study/ -q`
Expected: PASS — including the pre-existing study tests.

- [ ] **Step 5: Commit**

```bash
git add backend/features/study/study_guide.py tests/features/study/test_guide_relations.py
git commit -m "feat(study): a guide is a note, with the frontmatter to prove it"
```

---

## Task 7: The relink job

**Files:**
- Modify: `backend/features/ingest/store.py:64-72` (`JOB_KINDS`, `SLOT_GROUPS`)
- Modify: `backend/vault/writer.py:585-605` (`update_note`)
- Create: `backend/features/notes/relink.py`
- Create: `tests/features/notes/test_relink.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `store.JOB_KINDS` includes `"relink"`; `store.SLOT_GROUPS["relink"] == "index"`
  - `writer.update_note(..., *, taxonomy=None, snapshot=True, log=True)`
  - `relink.relinkable_notes(vault_path, *, taxonomy=None) -> list[str]`
  - `relink.relink_one(vault_path, rel_path, *, resolve, neighbours, taxonomy=None, dry_run=False) -> bool`
  - `relink.run_relink_job(job_id, *, settings, index_factory, dry_run=False) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/features/notes/test_relink.py
"""Backfilling relationships onto notes written before the feature existed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import frontmatter
import pytest

from backend.features.notes import relink
from backend.vault import relations


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "15-Courses" / "ETHICS" / "notes").mkdir(parents=True)
    (tmp_path / "15-Courses" / "ETHICS" / "materials").mkdir(parents=True)
    (tmp_path / "15-Courses" / "ETHICS" / "materials" / "wk1.pdf").write_bytes(b"%PDF-")
    (tmp_path / "15-Courses" / "ETHICS" / "notes" / "wk1.notes.md").write_text(
        "---\ntitle: wk1 — summary\ntype: note\ngenerated_by: argus\n"
        "source: 15-Courses/ETHICS/materials/wk1.pdf\ncourse: ETHICS\n---\n\n"
        "The old body.\n\n[[wk1]]\n",
        encoding="utf-8",
    )
    (tmp_path / "15-Courses" / "ETHICS" / "notes" / "mine.md").write_text(
        "---\ntitle: my own notes\n---\n\nHand written. Do not touch.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def test_only_notes_argus_wrote_are_relinkable(vault: Path):
    found = relink.relinkable_notes(vault)
    assert "15-Courses/ETHICS/notes/wk1.notes.md" in found
    assert "15-Courses/ETHICS/notes/mine.md" not in found


def test_relinking_adds_the_region_and_keeps_the_body(vault: Path):
    changed = relink.relink_one(
        vault, "15-Courses/ETHICS/notes/wk1.notes.md",
        resolve=lambda name: None, neighbours=[],
    )
    assert changed is True
    post = frontmatter.load(vault / "15-Courses/ETHICS/notes/wk1.notes.md")
    assert "The old body." in post.content
    assert relations.FENCE_START in post.content
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in post.content
    assert "argus/note" in post["tags"]


def test_a_second_run_changes_nothing(vault: Path):
    path = vault / "15-Courses/ETHICS/notes/wk1.notes.md"
    relink.relink_one(vault, "15-Courses/ETHICS/notes/wk1.notes.md",
                      resolve=lambda name: None, neighbours=[])
    first = path.read_text(encoding="utf-8")
    changed = relink.relink_one(vault, "15-Courses/ETHICS/notes/wk1.notes.md",
                                resolve=lambda name: None, neighbours=[])
    assert changed is False
    assert path.read_text(encoding="utf-8") == first


def test_a_hand_edit_to_a_generated_note_survives_a_relink(vault: Path):
    rel = "15-Courses/ETHICS/notes/wk1.notes.md"
    path = vault / rel
    relink.relink_one(vault, rel, resolve=lambda name: None, neighbours=[])
    path.write_text(
        path.read_text(encoding="utf-8").replace("The old body.", "The old body.\n\nMy note."),
        encoding="utf-8",
    )
    relink.relink_one(vault, rel, resolve=lambda name: None, neighbours=[])
    content = path.read_text(encoding="utf-8")
    assert "My note." in content
    assert content.count(relations.FENCE_START) == 1


def test_dry_run_reports_without_writing(vault: Path):
    path = vault / "15-Courses/ETHICS/notes/wk1.notes.md"
    before = path.read_text(encoding="utf-8")
    changed = relink.relink_one(
        vault, "15-Courses/ETHICS/notes/wk1.notes.md",
        resolve=lambda name: None, neighbours=[], dry_run=True,
    )
    assert changed is True
    assert path.read_text(encoding="utf-8") == before


def test_a_user_note_is_refused_even_if_named_directly(vault: Path):
    """The guard is per-note, not only per-listing: a hand-picked path must
    not become a way past it."""
    path = vault / "15-Courses/ETHICS/notes/mine.md"
    before = path.read_text(encoding="utf-8")
    assert relink.relink_one(vault, "15-Courses/ETHICS/notes/mine.md",
                             resolve=lambda name: None, neighbours=[]) is False
    assert path.read_text(encoding="utf-8") == before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/features/notes/test_relink.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.features.notes.relink`

- [ ] **Step 3a: Widen the job vocabulary**

In `backend/features/ingest/store.py`:

```python
JOB_KINDS = ("ingest", "reindex", "guide", "exam", "relink")

SLOT_GROUPS: dict[str, str] = {"ingest": "index", "reindex": "index", "relink": "index"}
```

Add to the `SLOT_GROUPS` comment block:

```python
#: A relink is in the 'index' group because it re-upserts every note it
#: rewrites and takes a git snapshot, so it contends for both the embedding
#: model and the git index exactly as an ingest does.
```

- [ ] **Step 3b: Let `update_note` defer its snapshot**

In `backend/vault/writer.py`:

```python
def update_note(
    vault_path: Path,
    rel_path: str,
    expected_content: str,
    new_content: str,
    *,
    taxonomy: Taxonomy | None = None,
    snapshot: bool = True,
    log: bool = True,
) -> None:
    """Replace a note's full content iff it still matches what the client read.

    ``snapshot``/``log`` mirror :func:`create_note`'s, and exist for the same
    reason: a job that rewrites N notes takes **one** snapshot before the
    first, not N. ``_git_snapshot`` runs git with ``check=False``, so
    overlapping snapshots race on ``.git/index.lock`` and the loser fails
    silently — N per-file snapshots is not a slower undo point, it is an
    unreliable one. Both default to today's behaviour, so no existing caller
    changes.
    """
    tax = taxonomy or active_taxonomy()
    note = guard_user_path(vault_path, rel_path, taxonomy=tax)
    if not note.is_file():
        raise WriterMissing(f"{rel_path} does not exist")
    current = note.read_text(encoding="utf-8")
    if current != expected_content:
        raise WriterConflict(f"{rel_path} has changed since you loaded it — refresh")
    if snapshot:
        _git_snapshot(vault_path, f"edit note {rel_path}")
    note.write_text(new_content, encoding="utf-8")
    if log:
        _argus_log(vault_path, f"edited note {rel_path}", taxonomy=tax)
```

- [ ] **Step 3c: Write `backend/features/notes/relink.py`**

```python
"""Backfill relationships onto notes written before they existed.

Everything Argus wrote before this feature is a leaf: one source link with
its extension stripped, no tags, no concepts. Shipping the feature for new
notes only would leave a vault half-linked and the feature looking broken on
everything already in it.

Runs as ``kind='relink'`` on the job store the ingest and the reindex already
share, so it inherits the segmented progress readout, the one-at-a-time slot,
and stale-job reconciliation. It should hold that slot: it re-upserts every
note it rewrites and takes a git snapshot, so it contends for the embedding
model and the git index exactly as an ingest does.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import frontmatter

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.ingest import store
from backend.rag.neighbours import nearest_notes
from backend.vault import relations
from backend.vault.links import build_link_index
from backend.vault.sources import GENERATED_BY, generated_kind
from backend.vault.writer import snapshot_vault, update_note

logger = logging.getLogger("argus.vault")

#: How much of a note is used to find its neighbours -- the same budget the
#: ingest path uses, so a relinked note gets the same neighbours a freshly
#: ingested one would.
from backend.rag.neighbours import NEIGHBOUR_QUERY_CHARS  # noqa: E402


def _is_generated(post: frontmatter.Post, rel_path: str) -> bool:
    """Did Argus write this note?

    Frontmatter first, because it is what the writer actually stamps and what
    a user would have to remove deliberately. The suffix is a fallback for a
    guide, which had no frontmatter at all until this branch — that is the
    exact population this backfill exists for.
    """
    if str(post.metadata.get("generated_by") or "") == GENERATED_BY:
        return True
    return generated_kind(rel_path) is not None


def relinkable_notes(vault_path: Path, *, taxonomy: Taxonomy | None = None) -> list[str]:
    """Every note Argus wrote, vault-relative, sorted for a stable readout.

    A note the user wrote is never in this list and is refused again in
    :func:`relink_one`. One guard would be enough for the listing path; two
    are here because a caller can name a path directly.
    """
    tax = taxonomy or active_taxonomy()
    found: list[str] = []
    for file_path in vault_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(vault_path).as_posix()
        if any(part in tax.excluded_top_dirs for part in Path(rel).parts):
            continue
        try:
            post = frontmatter.load(file_path)
        except Exception:  # noqa: BLE001 - malformed YAML is not a reason to crash a job
            continue
        if _is_generated(post, rel):
            found.append(rel)
    return sorted(found)


def relink_one(
    vault_path: Path,
    rel_path: str,
    *,
    resolve: relations.Resolver,
    neighbours: Sequence[tuple[str, str]],
    taxonomy: Taxonomy | None = None,
    dry_run: bool = False,
) -> bool:
    """Rewrite one note's relationships. ``True`` when anything changed.

    Idempotent: the fenced region is replaced, never appended, and frontmatter
    is merged rather than overwritten, so a second run produces byte-identical
    output and returns ``False``.
    """
    tax = taxonomy or active_taxonomy()
    file_path = vault_path / rel_path
    if not file_path.is_file():
        return False
    try:
        post = frontmatter.load(file_path)
    except Exception:  # noqa: BLE001
        logger.warning("relink: %s has unreadable frontmatter, skipped", rel_path)
        return False
    if not _is_generated(post, rel_path):
        return False

    original = file_path.read_text(encoding="utf-8")
    source_rel = str(post.metadata.get("source") or rel_path)
    course = post.metadata.get("course")
    prose, topics = relations.parse_topics(relations.strip_section(post.content))
    built = relations.build_relations(
        topics=topics or _topics_from(post),
        resolve=resolve,
        neighbours=neighbours,
        source_rel_path=source_rel,
        note_rel_path=rel_path,
        course=str(course) if course else None,
        courses_dir=tax.courses,
    )
    kind = "guide" if str(post.metadata.get("type") or "") == "guide" else "note"
    if kind == "guide":
        built = relations.Relations(
            topics=built.topics,
            links=[link for link in built.links if link.kind != "source"],
        )
    front = relations.merge_frontmatter(
        dict(post.metadata), built, kind=kind, course=str(course) if course else None
    )
    content = relations.replace_section(prose, relations.render_section(built))
    rewritten = frontmatter.dumps(frontmatter.Post(content, **front)) + "\n"
    if rewritten == original:
        return False
    if dry_run:
        return True
    update_note(
        vault_path, rel_path, original, rewritten,
        taxonomy=tax, snapshot=False, log=False,
    )
    return True


def _topics_from(post: frontmatter.Post) -> list[str]:
    """Topics already in frontmatter, for a note relinked a second time.

    The model's ``## Topics`` section was consumed the first time round, so a
    re-run has nothing to parse. Reading them back is what makes the second
    run a genuine no-op rather than a quiet downgrade to no concepts at all.
    """
    raw = post.metadata.get("topics") or []
    if isinstance(raw, str):
        raw = [raw]
    return [topic for topic in (str(item).strip() for item in raw) if topic]


def run_relink_job(
    job_id: str,
    *,
    settings: Settings,
    index_factory: Any,
    dry_run: bool = False,
) -> None:
    """The body of one relink job. **Never raises.**

    Same contract, and the same reasoning, as
    ``backend.features.ingest.pipeline.run_ingest_job``: this runs on a daemon
    thread, so an exception escaping here would surface only in a log nobody
    is watching. It opens its own connection because
    :func:`backend.core.db.connect` binds one to its creating thread.
    """
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        job = store.get_job(conn, job_id)
        if job is None:
            logger.warning("relink job %s vanished before it ran", job_id)
            return
        store.start_job(conn, job_id)
        _run(conn, job, settings=settings, index_factory=index_factory, dry_run=dry_run)
    except Exception as exc:
        logger.exception("relink job %s failed", job_id)
        try:
            store.finish_job(conn, job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("relink job %s could not even record its own failure", job_id)
    finally:
        conn.close()


def _run(
    conn: Any,
    job: dict[str, Any],
    *,
    settings: Settings,
    index_factory: Any,
    dry_run: bool,
) -> None:
    """One snapshot, one link index, one embedding model, N notes."""
    tax = settings.taxonomy
    paths = relinkable_notes(settings.vault_path, taxonomy=tax)
    store.add_items(
        conn,
        job["id"],
        [{"filename": path.rsplit("/", 1)[-1], "path": path, "stage": "queued"} for path in paths],
    )
    job = store.get_job(conn, job["id"]) or job

    if not dry_run and paths:
        snapshot_vault(settings.vault_path, f"relink {len(paths)} generated note(s)")

    index = index_factory()
    link_index = build_link_index(settings.vault_path, taxonomy=tax)

    errors: dict[str, str] = {}
    for item in job["items"]:
        rel_path = item["path"] or item["filename"]
        try:
            store.advance_item(conn, item["id"], stage="summarizing", path=rel_path)
            body = (settings.vault_path / rel_path).read_text(encoding="utf-8")
            changed = relink_one(
                settings.vault_path,
                rel_path,
                resolve=lambda name, _from=rel_path: link_index.resolve(name, from_path=_from),
                neighbours=nearest_notes(
                    index,
                    settings.vault_path,
                    body[:NEIGHBOUR_QUERY_CHARS],
                    exclude={rel_path},
                    taxonomy=tax,
                ),
                taxonomy=tax,
                dry_run=dry_run,
            )
            chunks = 0
            if changed and not dry_run:
                # Re-upserted so retrieval sees the new links: the one-hop
                # expansion reads `wikilinks` off chunk metadata, which is
                # stale until the note is embedded again.
                chunks = index.upsert_file(settings.vault_path, rel_path)
            store.advance_item(
                conn, item["id"], stage="done" if changed else "skipped", chunks=chunks
            )
        except Exception as exc:  # one bad note must not abort the rest
            logger.warning("relink failed for %s: %s", rel_path, exc)
            errors[rel_path] = str(exc)
            store.advance_item(
                conn, item["id"], stage="failed", failed_stage="summarizing", error=str(exc)
            )

    total = len(job["items"])
    if errors and len(errors) == total:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    store.finish_job(
        conn,
        job["id"],
        status=status,
        error="; ".join(f"{path}: {reason}" for path, reason in errors.items()) or None,
    )
```

Move the `NEIGHBOUR_QUERY_CHARS` import up with the others (the inline import above is written where it is for readability in this plan; ruff will require it at the top).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/features/notes/ tests/features/ingest/ -q && .venv/Scripts/python -m ruff check backend/`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add backend/features/notes/relink.py backend/features/ingest/store.py backend/vault/writer.py tests/features/notes/test_relink.py
git commit -m "feat(notes): relink backfills the vault Argus already wrote"
```

---

## Task 8: The relink route and the CLI

**Files:**
- Modify: `backend/features/notes/router.py`
- Modify: `backend/cli.py:199-240`
- Modify: `tests/features/notes/test_relink.py`

**Interfaces:**
- Consumes: Task 7.
- Produces:
  - `POST /api/notes/relink` → `202` with `{"job_id": str, "notes": int}`; `409` when another index-group job holds the slot.
  - `build_notes_router(settings, index_factory=None, job_runner=None)` — one new optional parameter, defaulting to a daemon thread.
  - `argus relink [--dry-run]`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/features/notes/test_relink.py

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.features.notes.router import build_notes_router


def _client(settings, index) -> TestClient:
    app = FastAPI()
    # Synchronous job runner: the route answers 202 and the work is already
    # done by the time the test reads the vault, which is what makes this a
    # test of behaviour rather than of timing.
    app.include_router(
        build_notes_router(settings, lambda: index, job_runner=lambda run: run())
    )
    return TestClient(app)


def test_the_route_relinks_and_reports_how_many_notes_it_found(vault_settings, fake_index):
    client = _client(vault_settings, fake_index)
    response = client.post("/api/notes/relink")
    assert response.status_code == 202
    assert response.json()["notes"] == 1
    content = (vault_settings.vault_path / "15-Courses/ETHICS/notes/wk1.notes.md").read_text(
        encoding="utf-8"
    )
    assert relations.FENCE_START in content


def test_the_route_refuses_while_an_ingest_holds_the_index(vault_settings, fake_index):
    from backend.core.db import connect, init_schema
    from backend.features.ingest import store

    conn = connect(vault_settings.db_path)
    init_schema(conn)
    store.create_job(conn, target="00-Inbox/files", filenames=["a.pdf"], kind="ingest")
    conn.close()

    client = _client(vault_settings, fake_index)
    assert client.post("/api/notes/relink").status_code == 409
```

Add the two fixtures at the top of the file:

```python
@pytest.fixture
def vault_settings(vault: Path):
    """A Settings pointed at the throwaway vault, with a real sqlite db."""
    from backend.core.config import Settings

    return Settings(vault_path=vault, backend_port=8000)


class _FakeIndex:
    """Counts upserts so a test can assert the note was re-embedded."""

    def __init__(self) -> None:
        self.upserted: list[str] = []

    def upsert_file(self, vault_path, rel_path):
        self.upserted.append(rel_path)
        return 3

    def query(self, *args, **kwargs):
        return []


@pytest.fixture
def fake_index() -> _FakeIndex:
    return _FakeIndex()
```

**Note for the implementer:** check `Settings`'s real constructor in `backend/core/config.py` before writing this fixture and match it exactly — if it takes different arguments, use whatever the existing tests in `tests/features/ingest/` already use to build one, rather than inventing a second way.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/features/notes/test_relink.py -q -k route`
Expected: FAIL — `TypeError: build_notes_router() got an unexpected keyword argument 'job_runner'`

- [ ] **Step 3a: Add the route**

In `backend/features/notes/router.py`:

```python
class RelinkStarted(BaseModel):
    """The 202 body for ``POST /api/notes/relink``."""

    job_id: str
    #: How many generated notes the job will visit. Reported up front because
    #: "nothing happened" and "you have no generated notes" look identical in
    #: a progress readout otherwise.
    notes: int


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a synchronous call."""
    threading.Thread(target=run, daemon=True).start()
```

and inside `build_notes_router`, after the existing routes:

```python
    @router.post("/notes/relink", status_code=202, response_model=RelinkStarted)
    def relink_notes() -> RelinkStarted:
        """Re-derive relationships for every note Argus wrote.

        Takes the index slot: it re-embeds each note it rewrites and takes a
        git snapshot, so it contends with an ingest and a reindex. 409 is this
        codebase's idiom for "one at a time" — the same answer
        ``/api/index/reindex`` gives when an ingest is in the way.
        """
        if index_factory is None:
            raise HTTPException(
                status_code=503,
                detail="the search index is unavailable — relinking needs it to find neighbours",
            )
        paths = relinkable_notes(settings.vault_path, taxonomy=settings.taxonomy)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            blocking = store.running_job(conn, "relink")
            if blocking is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"a {blocking['kind']} is already using the index — "
                    "wait for it to finish",
                )
            job_id = store.create_job(
                conn,
                # A relink writes only into notes that already exist, so it
                # has no target folder; '' is the column's "not applicable".
                target="",
                filenames=[],
                kind="relink",
            )
        finally:
            conn.close()
        run_job(lambda: run_relink_job(job_id, settings=settings, index_factory=index_factory))
        return RelinkStarted(job_id=job_id, notes=len(paths))
```

Update the factory signature and imports:

```python
def build_notes_router(
    settings: Settings,
    index_factory: Callable[[], Any] | None = None,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    ...
    router = APIRouter(prefix="/api")
    run_job = job_runner or _default_job_runner
```

```python
import threading

from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.notes.relink import relinkable_notes, run_relink_job
```

- [ ] **Step 3b: Add the CLI subcommand**

In `backend/cli.py`, beside the `reindex` parser:

```python
    relink_parser = subparsers.add_parser(
        "relink",
        help="re-derive concept and source links for every note Argus wrote",
    )
    relink_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
```

and the handler, following whatever pattern `reindex` uses in `main()`:

```python
    if args.command == "relink":
        from backend.core.config import Settings
        from backend.core.db import connect, init_schema
        from backend.features.ingest import store
        from backend.features.notes.relink import relinkable_notes, run_relink_job
        from backend.rag.index import make_index_factory

        settings = Settings.load()
        paths = relinkable_notes(settings.vault_path, taxonomy=settings.taxonomy)
        if not paths:
            print("nothing to relink — no notes carry generated_by: argus")
            return 0
        print(f"{'would relink' if args.dry_run else 'relinking'} {len(paths)} note(s)")
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            job_id = store.create_job(conn, target="", filenames=[], kind="relink")
        finally:
            conn.close()
        run_relink_job(
            job_id,
            settings=settings,
            index_factory=make_index_factory(settings),
            dry_run=args.dry_run,
        )
        print("done" if not args.dry_run else "dry run complete — nothing written")
        return 0
```

**Note for the implementer:** `make_index_factory`'s real signature is in `backend/rag/index.py:358` — read it and match it, and mirror exactly how the existing `reindex` command builds its settings and index rather than inventing a second way.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/features/notes/ tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Wire the router's new parameter at the app factory**

Find where `build_notes_router` is called (`backend/app.py` or `backend/features/external/app.py` — grep for it) and leave the call unchanged: `job_runner` defaults to the daemon thread, so no call site edit is needed. Confirm with:

Run: `.venv/Scripts/python -m pytest tests/test_app.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/features/notes/router.py backend/cli.py tests/features/notes/test_relink.py
git commit -m "feat(notes): relink from the app and from the command line"
```

---

## Task 9: The RELINK NOTES control

**Files:**
- Modify: `web/lib/api.ts:945-995`
- Modify: `web/app/(dashboard)/sources/page.tsx:180-290`

**Interfaces:**
- Consumes: Task 8's `POST /api/notes/relink`.
- Produces: `relinkNotes(): Promise<RelinkStarted>` and `interface RelinkStarted { job_id: string; notes: number }`.

- [ ] **Step 1: Add the API binding**

In `web/lib/api.ts`, after the reindex block:

```ts
// --- Relink ---------------------------------------------------------------

export interface RelinkStarted {
  job_id: string;
  /** How many generated notes the job will visit. Reported up front because
   * "nothing happened" and "you have no generated notes" look identical in a
   * progress readout otherwise. */
  notes: number;
}

/** Re-derive concept, neighbour and source links for every note Argus wrote.
 * `POST /api/notes/relink` — 202, runs on a background thread; the returned
 * `job_id` feeds the same segmented readout an ingest uses.
 *
 * Throws `ApiError` with status 409 when an ingest or reindex holds the
 * index: a relink re-embeds every note it rewrites, so all three contend. */
export function relinkNotes() {
  return mutateJSON<RelinkStarted>("/api/notes/relink", {});
}
```

- [ ] **Step 2: Add the control to `/sources`**

Add the handler beside `indexMissing`:

```tsx
  const [relinking, setRelinking] = useState(false);

  /** Backfill relationships onto notes written before the feature existed.
   * Without this the vault stays half-linked: new notes carry a Related
   * section and everything older is still a leaf. */
  async function relinkGenerated() {
    setRelinking(true);
    try {
      const started = await relinkNotes();
      if (started.notes === 0) {
        show("relink :: nothing to do — no generated notes in this vault");
        return;
      }
      setJobId(started.job_id);
      show(`relink :: ${started.notes} note${started.notes === 1 ? "" : "s"}`);
    } catch (relinkError) {
      const conflict = relinkError instanceof ApiError && relinkError.status === 409;
      show(
        conflict
          ? "relink :: the index is busy — try again when the current job finishes"
          : `relink :: failed — ${relinkError instanceof Error ? relinkError.message : "backend offline?"}`,
        { tone: "error" },
      );
    } finally {
      setRelinking(false);
    }
  }
```

Add the button beside the existing `+ INGEST` button (around line 353), so it sits with the other page-level actions rather than inside the conditional "not indexed" banner:

```tsx
              <Button
                variant="ghost"
                size="sm"
                onClick={relinkGenerated}
                disabled={relinking}
                title="Re-derive concept and source links for every note Argus wrote"
              >
                {relinking ? "Relinking…" : "Relink notes"}
              </Button>
```

Add `relinkNotes` to the `@/lib/api` import list.

**Note for the implementer:** match the existing `Button` variants actually available in `web/components/ui/Button.tsx` — read it first; if there is no `ghost` variant, use whatever the page's secondary actions already use.

- [ ] **Step 3: Verify the frontend gates**

Run: `cd web && npx tsc --noEmit && npx next lint && npx next build`
Expected: all three clean

- [ ] **Step 4: Commit**

```bash
git add web/lib/api.ts "web/app/(dashboard)/sources/page.tsx"
git commit -m "feat(web): relink generated notes from where the sources live"
```

---

## Task 10: End-to-end, docs, and the full gate run

**Files:**
- Create: `web/e2e/relations.spec.ts`
- Create: `docs/notes-relationships.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Write the e2e spec**

Read an existing spec in `web/e2e/` first (`sources.spec.ts` or `ingest.spec.ts`) and follow its fixture and navigation conventions exactly. **One spec only** — the job store allows one index-group job at a time and 409s otherwise, so a spec that fires several ingest cycles back to back loses the second and third, and it surfaces as a missing row rather than an error.

```ts
// web/e2e/relations.spec.ts
import { expect, test } from "@playwright/test";

test("an ingested file's note links back into the vault", async ({ page }) => {
  // Follow the existing ingest spec's setup verbatim: navigate to /sources,
  // open the ingest dialog, attach the markdown fixture, pick a note style,
  // submit, and wait for the job readout to reach a terminal state.
  // Then assert the written note carries the fenced region.
  await page.goto("/sources");
  // ... ingest the fixture, per web/e2e/ingest.spec.ts ...
  const note = page.getByRole("link", { name: /\.summary$/ });
  await expect(note).toBeVisible();
  await note.click();
  await expect(page.getByText("Related")).toBeVisible();
  await expect(page.getByText(/Source/)).toBeVisible();
});
```

- [ ] **Step 2: Run the e2e suite**

Run: `cd web && npx playwright test`
Expected: the new spec passes. Two specs fail on `main` already and a third is the intermittent server-death problem in `docs/BUILD_STATE.md` — **report the delta against `main`, never the raw count**.

- [ ] **Step 3: Write `docs/notes-relationships.md`**

Cover, in the house voice (see `docs/calendar.md` for register):
- What the Related section is and why links live in the body.
- Resolved vs hollow links, and that clicking a hollow one creates the note from the Concept Template.
- The tag vocabulary: `argus/note`, `argus/guide`, `course/<CODE>`, `topic/<slug>`.
- `argus relink --dry-run`, then `argus relink`; and the `RELINK NOTES` button on `/sources`.
- That a hand-edit to a generated note's body survives a relink, and that notes without `generated_by: argus` are never touched.

- [ ] **Step 4: Update `CHANGELOG.md` and `README.md`**

Add an `### Added` entry under the current unreleased heading, matching the existing entry style. In `README.md`, add `relink` to the CLI command table.

- [ ] **Step 5: Full gate run**

```bash
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m pytest -q
cd web && npx tsc --noEmit && npx next lint && npx next build && npx playwright test
```

Record the pytest count before and after the branch. Expected: no regressions; new tests add to the total.

- [ ] **Step 6: Commit and open the PR**

```bash
git add web/e2e/relations.spec.ts docs/notes-relationships.md CHANGELOG.md README.md
git commit -m "docs(notes): how relationships work, and how to backfill them"
git push -u origin feature/notes-relationships
gh pr create --base main --title "feat: notes that link into the vault" --body "..."
```

**Note:** `gh` is not installed on this machine. Push the branch and open the PR through the GitHub web UI, or report the push and hand the PR link step back.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `relations.py` module + dataclasses | 1, 2, 3 |
| Prompt tail, `parse_topics`, degradation | 1, 5 |
| `normalise_topic` | 1 |
| Resolution, path-qualification | 2 |
| Neighbours, `expand_links=False` | 4 |
| Source link `.pdf` bug, course link | 2, 5 |
| Fence, body-vs-frontmatter, tags | 3 |
| Study guides | 6 |
| Relink: guard, snapshot, merge, idempotence, index, surfaces | 7, 8, 9 |
| I3 | 4 |
| Testing table | 1–8, 10 |
| Out of scope (no `Taxonomy.knowledge`) | Global Constraints |

No gaps.

**Placeholder scan:** the two `**Note for the implementer:**` blocks (Task 8's `Settings`/`make_index_factory` signatures, Task 9's `Button` variants) and Task 10's e2e body point at real files to read rather than describing work vaguely — they exist because inventing a signature that does not match the codebase is worse than saying "read this file". Task 10's docs steps enumerate exact content. No TBDs.

**Type consistency:** `Relation`/`Relations`/`Resolver` are defined in Task 2 and used unchanged in 3, 5, 6, 7. `MAX_NEIGHBOURS` is defined in Task 1 and imported by `rag/neighbours.py` in Task 4. `GENERATED_BY` moves to `vault/sources.py` in Task 3 and is imported by 6 and 7. `nearest_notes`'s signature is fixed in Task 4 and called identically in 5 and 7. `relinkable_notes` / `run_relink_job` are defined in Task 7 and called in 8. `RelinkStarted` matches between Task 8 (Python) and Task 9 (TypeScript).
