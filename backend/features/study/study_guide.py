"""Study guides: cited synthesis of course materials + a notes-gap list."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.ingest.notes import COURSE_NOTE_SUFFIX
from backend.features.study.practice_exam import (
    MAX_PROMPT_CHARS,
    Generator,
    StudyError,
)

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
    return rel_path.endswith(COURSE_NOTE_SUFFIX)


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
    return f"""Write a study guide for course {course}, scope: {scope}.
Use ONLY the source excerpts below. Structure (markdown):

1. `## Outline` — the topic map.
2. `## Key concepts` — each with a one-line definition and a citation like
   [<file> p.N] / [<file> slide N] / [<path>] taken from the SOURCE markers.
3. `## Worked examples` — 2-3 step-by-step examples from the material.

Every factual claim needs a citation. Do not invent content.

SOURCES:
{"".join(excerpts)}"""


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

    gaps = notes_gap_list(corpus)
    if gaps:
        body += "\n\n## What you haven't taken notes on\n\n"
        body += "\n".join(f"- [ ] {gap}" for gap in gaps)

    study_dir = vault_path / tax.course_study(course)
    study_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", scope.lower()).strip("-")[:40] or "guide"
    name = f"guide-{slug}-{date.today().isoformat()}.md"
    (study_dir / name).write_text(body + "\n", encoding="utf-8")
    return f"{tax.course_study(course)}/{name}"
