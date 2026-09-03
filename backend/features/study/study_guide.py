"""Study guides: cited synthesis of course materials + a notes-gap list."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter

from backend.agent.formatting import compose, math_contract, note_quality, topics_tail
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.study.practice_exam import (
    MAX_PROMPT_CHARS,
    Generator,
    StudyError,
    unique_base,
)
from backend.vault import relations
from backend.vault.sources import GENERATED_BY, generated_kind

#: Heading the notes-gap checklist is written under. A constant because the
#: renderer writes it and the test that pins where it lands reads it.
GAP_HEADING = "## What you haven't taken notes on"

#: A reply that is *entirely* one fenced block, and nothing else. Small models
#: habitually wrap a whole markdown answer in ```` ```markdown ````, so
#: unwrapping that is worth doing -- but only that.
_WHOLE_REPLY_FENCE = re.compile(r"\A```[^\n]*\n(.*?)\n?```\Z", re.DOTALL)


def _is_generated(rel_path: str) -> bool:
    """Did Argus write this note, rather than the user?

    ``notes_gap_list`` below answers "what have you not taken notes on yet",
    and it answers it by treating everything under ``notes/`` as the user's
    own notes. Once ingestion started writing generated notes into that same
    zone (see :mod:`backend.features.ingest.notes`), that question silently
    answered itself: the AI note covering a lecture made the lecture look
    covered. Matched on the filename suffix rather than frontmatter because
    chunk metadata carries no ``generated_by`` field -- which is exactly why
    the suffix is distinct in the first place.
    """
    return generated_kind(rel_path) is not None


def notes_gap_list(corpus: list[dict[str, Any]]) -> list[str]:
    """Topics present in materials/ with no matching chunk in notes/.

    Cheap lexical comparison: a material chunk's heading/title counts as
    "covered" when its keywords appear in any notes chunk.
    """
    notes_text = " ".join(
        chunk["text"].lower()
        for chunk in corpus
        if "/notes/" in str(chunk["meta"].get("path", ""))
        and not _is_generated(str(chunk["meta"].get("path", "")))
    )
    gaps: list[str] = []
    for chunk in corpus:
        path = str(chunk["meta"].get("path", ""))
        if "/materials/" not in path:
            continue
        topic = str(chunk["meta"].get("heading") or chunk["meta"].get("title") or "").strip()
        if not topic:
            first_line = chunk["text"].strip().splitlines()[0][:80]
            topic = first_line
        keywords = [word for word in re.findall(r"[a-z]{5,}", topic.lower())][:3]
        if not keywords:
            continue
        covered = notes_text and all(word in notes_text for word in keywords)
        label = f"{topic} ({chunk['meta'].get('path', '').rsplit('/', 1)[-1]})"
        if not covered and label not in gaps:
            gaps.append(label)
    return gaps[:20]


def _unwrap_fenced_reply(raw: str) -> str:
    """Undo a model fencing its *whole* answer, and nothing more.

    This deliberately is not ``practice_exam._strip_fences``. That function
    answers "where is the JSON payload in this reply", so it searches for the
    first fence anywhere and returns its contents -- correct for an exam,
    catastrophic for a guide. A guide is prose that may legitimately *contain*
    a fence: the prompt asks for step-by-step worked examples, which for a CS
    course is a code block. Run through the extractor, such a guide had its
    outline, concepts and citations replaced by the body of that one block,
    silently, and that is what got written to the vault.

    So the match is anchored to both ends: unwrap only when the fence *is* the
    reply. Anything else is returned untouched.
    """
    match = _WHOLE_REPLY_FENCE.match(raw.strip())
    return match.group(1) if match else raw


def guide_prompt(course: str, scope: str, corpus: list[dict[str, Any]]) -> str:
    excerpts: list[str] = []
    used = 0
    for chunk in corpus:
        meta = chunk["meta"]
        where = (
            f"p.{meta['page']}"
            if meta.get("page")
            else (f"slide {meta['slide']}" if meta.get("slide") else "note")
        )
        block = f"[SOURCE {meta.get('path')} {where}]\n{chunk['text']}\n"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        excerpts.append(block)
        used += len(block)
    structure = f"""Write a study guide for course {course}, scope: {scope}.
Use ONLY the source excerpts below. Structure (markdown):

1. `## Outline` — the topic map.
2. `## Key concepts` — each with a one-line definition and a citation like
   [<file> p.N] / [<file> slide N] / [<path>] taken from the SOURCE markers.
3. `## Worked examples` — 2-3 step-by-step examples from the material, each
   step saying *why* it follows from the one above it.
4. `## Common mistakes` — the errors this material invites, where the sources
   name or imply them. Omit the section rather than inventing one.

Every factual claim needs a citation."""

    # The same three contracts the per-document note styles get
    # (backend/features/ingest/notes.py). A course guide and the note sitting
    # next to it in the vault are the same kind of artefact and are held to the
    # same rules; two copies of "here is how to write a note" is how they stop
    # being. `topics_tail` is the newest of the three and the one a guide most
    # obviously needs: a guide covers a whole course, so the concepts it names
    # are the concepts the course is about.
    #
    # The tail goes above SOURCES rather than below it because every contract
    # block belongs to the instruction and the excerpts are the material -- and
    # because `parse_topics` reads the *last* "## Topics" heading, so a source
    # excerpt that happens to contain one costs nothing.
    return compose(
        structure,
        note_quality(),
        math_contract(),
        topics_tail(),
        f"SOURCES:\n{''.join(excerpts)}",
    )


def _cited_materials(corpus: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(rel_path, title)`` for the distinct files a guide's corpus came from.

    Rank order is preserved and duplicates collapse, because the corpus is a
    list of *chunks*: a lecture deck that contributed four passages is one
    neighbour, not four, and the passage that ranked highest is the reason the
    deck is here at all.
    """
    cited: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in corpus:
        meta = chunk.get("meta") or {}
        rel_path = str(meta.get("path") or "")
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        cited.append((rel_path, str(meta.get("title") or rel_path.rsplit("/", 1)[-1])))
    return cited


def guide_markdown(
    course: str,
    scope: str,
    body: str,
    corpus: list[dict[str, Any]],
    *,
    resolve: relations.Resolver | None = None,
    taxonomy: Taxonomy | None = None,
    gaps: Sequence[str] = (),
) -> str:
    """One guide as a complete note: frontmatter, prose, fenced Related region.

    Guides used to be written as ``body + "\\n"`` -- no title, no ``type``, no
    course, no links, no tags -- which left them reachable by full-text search
    and by nothing else, while the ingest note in the next folder carried a
    full header. A guide is the same kind of artefact as that note, so it is
    rendered through the same module.

    A guide needs **no index**, which is why this takes plain data and no
    ``VaultIndex``. It already holds the corpus chunks it cited, so the
    materials it actually drew from are its neighbours -- a better answer than
    a fresh similarity query, which would return what merely *reads* like the
    guide rather than what the guide was written from.

    ``gaps`` is :func:`notes_gap_list`'s output rather than something computed
    here, and it is spliced in *after* the topics come off. That ordering is
    load-bearing: the prompt asks for ``## Topics`` last, so a checklist
    appended to the model's reply lands below that heading, where
    :func:`~backend.vault.relations.parse_topics` reads every ``- `` line as a
    concept name -- and then returns the body truncated at the heading,
    deleting the checklist outright.
    """
    tax = taxonomy or active_taxonomy()
    prose, topics = relations.parse_topics(body.strip())
    if gaps:
        prose = prose.rstrip() + f"\n\n{GAP_HEADING}\n\n"
        prose += "\n".join(f"- [ ] {gap}" for gap in gaps) + "\n"

    # A guide has no single source file, and `source_rel_path` is only read for
    # two things: the neighbour exclusion set, and one `source` link. Passing
    # the guide's own path makes the first correct -- a guide never cites
    # itself -- and makes the second a self-link, which is dropped below rather
    # than shown. Filtering after the fact is the honest option while
    # `build_relations` has no way to say "this artefact has no source"; the
    # alternative, nominating the top-ranked material as *the* source, would
    # both be arbitrary and silently cost that material its place in the
    # neighbour list.
    cited = _cited_materials(corpus)
    self_path = f"{tax.course_study(course)}/guide.md"
    built = relations.build_relations(
        topics=topics,
        resolve=resolve or (lambda _name: None),
        neighbours=cited,
        source_rel_path=self_path,
        note_rel_path=self_path,
        course=course,
        taxonomy=tax,
    )
    built = relations.Relations(
        topics=built.topics,
        links=[link for link in built.links if link.kind != "source"],
    )

    front: dict[str, Any] = {
        "title": f"{course} — {scope}",
        "type": "guide",
        "generated_by": GENERATED_BY,
        "course": course,
        "scope": scope,
    }
    if cited:
        # What this guide was actually written from, recorded because it
        # cannot be recovered later. A relink recomputes a note's neighbours
        # with a similarity query, which is right for a note but wrong for a
        # guide: a guide's neighbours are the materials it cited, the corpus
        # is gone by the time a relink runs, and without this the first relink
        # would quietly replace "what this was written from" with "what reads
        # like this". `relink` reads this key back for exactly that reason.
        front["sources"] = [path for path, _title in cited]
    front = relations.merge_frontmatter(front, built, kind="guide", course=course)
    content = relations.replace_section(prose, relations.render_section(built))
    return frontmatter.dumps(frontmatter.Post(content, **front)) + "\n"


async def generate_study_guide(
    vault_path: Path,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    scope: str = "everything so far",
    *,
    taxonomy: Taxonomy | None = None,
) -> str:
    """Generate and write ``study/guide-<scope>-<date>.md``; returns its vault path."""
    tax = taxonomy or active_taxonomy()
    if not corpus:
        raise StudyError(f"no indexed material for course {course} — upload to materials/ first")

    body = _unwrap_fenced_reply(await generator(guide_prompt(course, scope, corpus))).strip()
    if not body:
        raise StudyError("generator returned an empty guide")

    # Rendered before the destination is chosen, so a guide that cannot be
    # rendered never claims a filename. The gap list is handed over rather than
    # appended here: appended, it would land under the model's trailing
    # `## Topics` heading and be read back as three more concept names.
    markdown = guide_markdown(
        course, scope, body, corpus, taxonomy=tax, gaps=notes_gap_list(corpus)
    )

    study_dir = vault_path / tax.course_study(course)
    study_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", scope.lower()).strip("-")[:40] or "guide"
    # Counted past a collision rather than written over. The name is course +
    # scope + day by construction, so generating a guide twice in one day --
    # which is exactly what re-running after tweaking the selection does --
    # silently destroyed the first one. The exam path has always done this.
    name = f"{unique_base(study_dir, f'guide-{slug}-{date.today().isoformat()}')}.md"
    (study_dir / name).write_text(markdown, encoding="utf-8")
    return f"{tax.course_study(course)}/{name}"
