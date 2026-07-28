"""Which vault paths may enter the index (privacy invariant I3)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

EXCLUDED_TOP_DIRS = {
    "99-Private",  # I3: never indexed, never sent to any model
    "90-Meta",  # D2: dev journal is served by the journal API, not RAG
    ".obsidian",
    ".argus",
    ".git",
    ".trash",
}
INDEXABLE_SUFFIXES = {".md", ".pdf", ".pptx", ".docx"}


def is_indexable(rel_path: str | Path) -> bool:
    """True when a vault-relative path is allowed into the RAG index."""
    parts = PurePosixPath(Path(rel_path).as_posix()).parts
    if not parts or any(part in EXCLUDED_TOP_DIRS for part in parts):
        return False
    return PurePosixPath(parts[-1]).suffix.lower() in INDEXABLE_SUFFIXES
