"""Markdown-aware chunking with rich retrieval metadata.

Chunks target ~350 tokens (word-count proxy) with overlap, split on headings
first. Every chunk carries the metadata hybrid retrieval and citations need:
path, title, heading, date, tags, wikilinks, course code, page/slide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.rag.extract import Block

TARGET_WORDS = 260  # ~350 tokens
OVERLAP_WORDS = 40  # ~50 tokens
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
COURSES_DIR = "15-Courses"


@dataclass
class Chunk:
    """One indexable chunk of text plus its metadata payload."""

    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading, body)`` sections; preamble heading is ''."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            sections.append((match.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if "\n".join(lines).strip()
    ]


def _windows(words: list[str]) -> list[str]:
    """Overlapping word windows targeting TARGET_WORDS."""
    if len(words) <= TARGET_WORDS:
        return [" ".join(words)]
    windows: list[str] = []
    start = 0
    while start < len(words):
        window = words[start : start + TARGET_WORDS]
        windows.append(" ".join(window))
        if start + TARGET_WORDS >= len(words):
            break
        start += TARGET_WORDS - OVERLAP_WORDS
    return windows


def _note_date(front: dict[str, Any], rel_path: str) -> str | None:
    for key in ("date", "created"):
        value = front.get(key)
        if value:
            return str(value)[:10]
    match = DATE_IN_NAME_RE.search(rel_path)
    return match.group(1) if match else None


def _course_of(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] == COURSES_DIR:
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


def chunk_blocks(blocks: list[Block], rel_path: str) -> list[Chunk]:
    """Chunk extracted blocks for one vault-relative file path."""
    chunks: list[Chunk] = []
    for block in blocks:
        front = block.meta.get("frontmatter", {}) or {}
        tags = front.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        base_meta: dict[str, Any] = {
            "path": rel_path,
            "title": _title_of(front, block.text, rel_path),
            "date": _note_date(front, rel_path) or "",
            "tags": ",".join(str(tag) for tag in tags),
            "wikilinks": ",".join(dict.fromkeys(WIKILINK_RE.findall(block.text))),
            "course": _course_of(rel_path) or "",
        }
        for key in ("page", "slide"):
            if key in block.meta:
                base_meta[key] = block.meta[key]

        pieces = _sections(block.text) if "frontmatter" in block.meta else [("", block.text)]
        for heading, body in pieces:
            for window in _windows(body.split()):
                if not window.strip():
                    continue
                chunks.append(Chunk(text=window, meta={**base_meta, "heading": heading}))
    return chunks
