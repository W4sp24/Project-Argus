"""Read-only vault note listing.

Walks the vault for markdown files, resolving a human title for each. The
privacy boundary starts here: ``99-Private/`` and app/internal folders are
never surfaced (invariant I3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from pydantic import BaseModel

EXCLUDED_DIRS = {".obsidian", ".argus", ".git", ".trash", "99-Private"}


class NoteInfo(BaseModel):
    """Summary of one markdown note in the vault."""

    path: str
    title: str
    folder: str
    modified: str


def _is_excluded(relative: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in relative.parts)


def _resolve_title(file_path: Path) -> str:
    """Frontmatter ``title`` > first H1 > filename stem."""
    try:
        post = frontmatter.load(file_path)
    except Exception:  # malformed frontmatter must never break listing
        return file_path.stem
    title = post.metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in post.content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return file_path.stem


def list_notes(vault_path: Path) -> list[NoteInfo]:
    """All non-private markdown notes in the vault, newest first."""
    notes: list[NoteInfo] = []
    for file_path in vault_path.rglob("*.md"):
        relative = file_path.relative_to(vault_path)
        if _is_excluded(relative):
            continue
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).isoformat()
        notes.append(
            NoteInfo(
                path=relative.as_posix(),
                title=_resolve_title(file_path),
                folder=relative.parent.as_posix() if relative.parent != Path(".") else "",
                modified=modified,
            )
        )
    notes.sort(key=lambda note: note.modified, reverse=True)
    return notes
