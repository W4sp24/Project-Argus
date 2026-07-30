"""Read-only vault note listing.

Walks the vault for markdown files, resolving a human title for each. The
privacy boundary starts here: the taxonomy's private/journal dirs (see
:mod:`backend.core.taxonomy`) and app/internal folders are never surfaced
(invariant I3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    # Opt-in only (``list_notes(..., frontmatter_keys=...)``): stays ``None`` —
    # and, via the route's ``response_model_exclude_none``, absent from the
    # JSON body entirely — for every caller that doesn't ask for it, so the
    # default response is byte-identical to before this field existed.
    frontmatter: dict[str, Any] | None = None


def _is_excluded(relative: Path, excluded_dirs: frozenset[str]) -> bool:
    return any(part in excluded_dirs for part in relative.parts)


def _resolve_title(post: frontmatter.Post | None, file_path: Path) -> str:
    """Frontmatter ``title`` > first H1 > filename stem."""
    if post is not None:
        title = post.metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        for line in post.content.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return file_path.stem


def list_notes(
    vault_path: Path,
    *,
    taxonomy: Taxonomy | None = None,
    folder: str | None = None,
    frontmatter_keys: Sequence[str] | None = None,
) -> list[NoteInfo]:
    """All non-private markdown notes in the vault, newest first.

    ``folder`` (vault-relative, e.g. ``30-Areas/papers``) restricts the
    listing to notes directly inside that folder or any of its subfolders.
    ``frontmatter_keys`` is an opt-in whitelist: only these keys (when
    present in a note's frontmatter) are attached under ``NoteInfo.frontmatter``.
    Neither parameter is passed by the historical callers (``useNotes()`` and
    friends), so their listing is unaffected — see ``NoteInfo.frontmatter``
    for how the JSON stays byte-identical too.
    """
    tax = taxonomy or active_taxonomy()
    excluded_dirs = tax.excluded_top_dirs
    normalized_folder = folder.strip("/").replace("\\", "/") if folder else None
    notes: list[NoteInfo] = []
    for file_path in vault_path.rglob("*.md"):
        relative = file_path.relative_to(vault_path)
        if _is_excluded(relative, excluded_dirs):
            continue
        folder_field = relative.parent.as_posix() if relative.parent != Path(".") else ""
        if normalized_folder is not None and not (
            folder_field == normalized_folder or folder_field.startswith(f"{normalized_folder}/")
        ):
            continue
        try:
            post = frontmatter.load(file_path)
        except Exception:  # malformed frontmatter must never break listing
            post = None
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).isoformat()
        fm: dict[str, Any] | None = None
        if frontmatter_keys:
            fm = {
                key: post.metadata[key]
                for key in frontmatter_keys
                if post is not None and key in post.metadata
            }
        notes.append(
            NoteInfo(
                path=relative.as_posix(),
                title=_resolve_title(post, file_path),
                folder=folder_field,
                modified=modified,
                frontmatter=fm,
            )
        )
    notes.sort(key=lambda note: note.modified, reverse=True)
    return notes
