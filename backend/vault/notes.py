"""Read-only vault note listing.

Walks the vault for markdown files, resolving a human title for each. The
privacy boundary starts here: the taxonomy's private/journal dirs (see
:mod:`backend.core.taxonomy`) and app/internal folders are never surfaced
(invariant I3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy

# Deprecated for 0.3 — bound to Taxonomy()'s defaults; prefer settings.taxonomy
# / active_taxonomy(). Kept so a straggler importing this name directly still
# resolves, at the cost of not reflecting a custom taxonomy.
EXCLUDED_DIRS = Taxonomy().excluded_top_dirs


class NoteInfo(BaseModel):
    """Summary of one markdown note in the vault."""

    path: str
    title: str
    folder: str
    modified: str


def _is_excluded(relative: Path, excluded_dirs: frozenset[str]) -> bool:
    return any(part in excluded_dirs for part in relative.parts)


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


def list_notes(vault_path: Path, *, taxonomy: Taxonomy | None = None) -> list[NoteInfo]:
    """All non-private markdown notes in the vault, newest first."""
    tax = taxonomy or active_taxonomy()
    excluded_dirs = tax.excluded_top_dirs
    notes: list[NoteInfo] = []
    for file_path in vault_path.rglob("*.md"):
        relative = file_path.relative_to(vault_path)
        if _is_excluded(relative, excluded_dirs):
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
