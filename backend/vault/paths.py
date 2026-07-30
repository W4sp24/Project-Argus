"""Which vault paths may enter the index (privacy invariant I3)."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from backend.core.taxonomy import Taxonomy, active_taxonomy


def is_indexable(rel_path: str | Path, *, taxonomy: Taxonomy | None = None) -> bool:
    """True when a vault-relative path is allowed into the RAG index."""
    tax = taxonomy or active_taxonomy()
    parts = PurePosixPath(Path(rel_path).as_posix()).parts
    if not parts or any(part in tax.excluded_top_dirs for part in parts):
        return False
    return PurePosixPath(parts[-1]).suffix.lower() in tax.indexable_suffixes


# --- Back-compat aliases (deprecated for 0.3) --------------------------------
# Bound to Taxonomy()'s defaults, so a straggler importing these directly
# (rather than `settings.taxonomy` / `active_taxonomy()`) keeps working —
# they just stop reflecting a custom taxonomy, same as before this module
# was configurable.
EXCLUDED_TOP_DIRS = Taxonomy().excluded_top_dirs
INDEXABLE_SUFFIXES = Taxonomy().indexable_suffixes
