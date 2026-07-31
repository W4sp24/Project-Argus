"""Markdown-aware chunking with rich retrieval metadata.

Chunks target ~350 tokens (word-count proxy) with overlap, split on headings
first, and windowed *by whole line* rather than by word — a bullet list, a
table, a fenced code block or an Obsidian callout is made of lines that carry
meaning; joining them back with spaces (the old ``" ".join(words)`` scheme)
collapsed every one of those into a single run-on line, which is bad for the
model and bad for the citation snippets the UI shows. A fenced code block
(```` ``` ````) is always kept whole — windowing never splits inside one,
even if that means the chunk exceeds the target size.

Every chunk carries the metadata hybrid retrieval and citations need: path,
title, heading, breadcrumb (heading ancestry), date, tags (frontmatter ∪
inline ``#tags``), wikilinks, course code, page/slide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.extract import Block

TARGET_WORDS = 260  # ~350 tokens
OVERLAP_WORDS = 40  # ~50 tokens
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
FENCE_RE = re.compile(r"^\s*```")

# An inline Obsidian tag: a '#' immediately (no space) followed by a word
# char, then more word chars / '-' / '/' (nesting, e.g. "#project/argus").
# The negative lookbehind excludes a '#' that is itself preceded by a word
# char, '#' or '/' — which is exactly what a URL fragment
# ("https://x.com/page#anchor") looks like, so those are never captured.
# A Markdown heading ("# Heading", "## Heading") is naturally excluded too:
# its '#' run is always followed by a space, and a space never matches the
# "word char immediately after '#'" requirement below.
INLINE_TAG_RE = re.compile(r"(?<![\w#/])#([A-Za-z][\w/-]*)")

# Deprecated for 0.3 — bound to Taxonomy()'s default; prefer
# settings.taxonomy.courses / active_taxonomy().courses.
COURSES_DIR = Taxonomy().courses


@dataclass
class Chunk:
    """One indexable chunk of text plus its metadata payload."""

    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _sections(text: str) -> list[tuple[str, str, int, list[str]]]:
    """Split markdown into ``(heading, breadcrumb, level, body_lines)`` sections.

    ``breadcrumb`` is the heading ancestry — this section's own heading and
    every ancestor above it, joined with ``" > "`` — tracked with a level
    stack so nested headings (``##`` under a ``#``, ``###`` under that, ...)
    produce a correct chain rather than just the immediate heading. The
    preamble before the first heading has heading/breadcrumb ``""`` and
    level ``0``. Sections with no body text are dropped, same as before —
    only *what's captured about a kept section* changed here.
    """
    sections: list[tuple[str, str, int, list[str]]] = [("", "", 0, [])]
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            breadcrumb = " > ".join(h for _, h in stack)
            sections.append((heading, breadcrumb, level, []))
        else:
            sections[-1][3].append(line)
    return [
        (heading, breadcrumb, level, lines)
        for heading, breadcrumb, level, lines in sections
        if "\n".join(lines).strip()
    ]


def _line_words(line: str) -> int:
    return len(line.split())


def _split_long_line(line: str) -> list[str]:
    """Word-split a single line longer than TARGET_WORDS words.

    Obsidian doesn't hard-wrap, so one paragraph is often one very long
    line; without this, that one line would become its own (oversized)
    chunk instead of being windowed like the old word-based chunker did. A
    short line (any bullet, table row, or ordinary prose line — the
    overwhelming common case) is returned unchanged.
    """
    words = line.split()
    if len(words) <= TARGET_WORDS:
        return [line]
    pieces: list[str] = []
    start = 0
    while start < len(words):
        end = start + TARGET_WORDS
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - OVERLAP_WORDS
    return pieces


def _atomic_units(lines: list[str]) -> list[list[str]]:
    """Group lines into windowing units.

    A fenced code block (opening ```` ``` ```` to its matching close, or to
    end-of-text if unterminated) is one unit that later windowing must never
    split apart. Any other line becomes its own unit, except a single line
    longer than TARGET_WORDS words, which is pre-split (see
    :func:`_split_long_line`) into several word-windowed units so an
    unwrapped giant paragraph still chunks.
    """
    units: list[list[str]] = []
    i, n = 0, len(lines)
    while i < n:
        if FENCE_RE.match(lines[i]):
            j = i + 1
            while j < n and not FENCE_RE.match(lines[j]):
                j += 1
            end = min(j + 1, n)  # include the closing fence line when present
            units.append(lines[i:end])
            i = end
        else:
            for piece in _split_long_line(lines[i]):
                units.append([piece])
            i += 1
    return units


def _windows(lines: list[str]) -> list[str]:
    """Overlapping line-based windows targeting TARGET_WORDS.

    Whole lines (or whole fenced blocks — see :func:`_atomic_units`) are the
    unit of accumulation and overlap, so newlines inside a chunk are always
    preserved and a fence is never split across two chunks.
    """
    units = _atomic_units(lines)
    if not units:
        return []
    unit_words = [sum(_line_words(line) for line in unit) for unit in units]
    if sum(unit_words) <= TARGET_WORDS:
        return ["\n".join(line for unit in units for line in unit)]

    windows: list[str] = []
    n = len(units)
    start = 0
    while start < n:
        count, end = 0, start
        while end < n and (count == 0 or count + unit_words[end] <= TARGET_WORDS):
            count += unit_words[end]
            end += 1
        windows.append("\n".join(line for unit in units[start:end] for line in unit))
        if end >= n:
            break
        # Overlap: walk back from `end` accumulating trailing units until
        # ~OVERLAP_WORDS is covered, without regressing before `start`.
        overlap, back = 0, end
        while back > start and overlap < OVERLAP_WORDS:
            back -= 1
            overlap += unit_words[back]
        start = back if back > start else end
    return windows


def _strip_fences(text: str) -> str:
    """Blank out fenced code blocks so tag/heading scanning ignores their contents."""
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def _inline_tags(text: str) -> list[str]:
    """Every inline ``#tag`` in ``text``, excluding fenced code, headings, and URL fragments."""
    return [match.group(1) for match in INLINE_TAG_RE.finditer(_strip_fences(text))]


def _note_date(front: dict[str, Any], rel_path: str) -> str | None:
    for key in ("date", "created"):
        value = front.get(key)
        if value:
            return str(value)[:10]
    match = DATE_IN_NAME_RE.search(rel_path)
    return match.group(1) if match else None


def _course_of(rel_path: str, courses_dir: str) -> str | None:
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] == courses_dir:
        return parts[1]
    return None


def _title_of(front: dict[str, Any], text: str, rel_path: str) -> str:
    title = front.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def chunk_blocks(
    blocks: list[Block], rel_path: str, *, taxonomy: Taxonomy | None = None
) -> list[Chunk]:
    """Chunk extracted blocks for one vault-relative file path."""
    tax = taxonomy or active_taxonomy()
    chunks: list[Chunk] = []
    for block in blocks:
        front = block.meta.get("frontmatter", {}) or {}
        front_tags = front.get("tags") or []
        if isinstance(front_tags, str):
            front_tags = [front_tags]
        front_tags = [str(tag).strip().lstrip("#") for tag in front_tags if str(tag).strip()]
        # Only frontmatter tags were ever captured before; in a real vault
        # most tags are written inline (``#project/argus``) in the body, so
        # union the two rather than trusting frontmatter alone. Order-stable
        # de-dupe: frontmatter tags first, then inline tags not already
        # present (exact string match — case/whitespace folding happens at
        # filter time in rag/retrieve.py, not at capture time).
        all_tags = list(dict.fromkeys([*front_tags, *_inline_tags(block.text)]))
        base_meta: dict[str, Any] = {
            "path": rel_path,
            "title": _title_of(front, block.text, rel_path),
            "date": _note_date(front, rel_path) or "",
            "tags": ",".join(all_tags),
            "wikilinks": ",".join(dict.fromkeys(WIKILINK_RE.findall(block.text))),
            "course": _course_of(rel_path, tax.courses) or "",
        }
        for key in ("page", "slide"):
            if key in block.meta:
                base_meta[key] = block.meta[key]

        pieces = (
            _sections(block.text)
            if "frontmatter" in block.meta
            else [("", "", 0, block.text.splitlines())]
        )
        for heading, breadcrumb, level, lines in pieces:
            heading_line = f"{'#' * level} {heading}" if heading else None
            for window in _windows(lines):
                if not window.strip():
                    continue
                text = f"{heading_line}\n{window}" if heading_line else window
                meta = {**base_meta, "heading": heading, "breadcrumb": breadcrumb}
                chunks.append(Chunk(text=text, meta=meta))
    return chunks
