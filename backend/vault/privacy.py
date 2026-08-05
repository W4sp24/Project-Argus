"""The shared privacy predicate (invariant I3): ``99-Private/`` and ``#no-ai``.

I3 says a private note is *never indexed and never sent anywhere*. Before this
module existed that rule was implemented twice, privately, with byte-identical
logic: :mod:`backend.rag.extract` (``_is_private``) and
:mod:`backend.vault.journal` (``_load_visible``). The *directory* half of the
rule lived separately again, in :func:`backend.vault.paths.is_indexable` (via
:attr:`backend.core.taxonomy.Taxonomy.excluded_top_dirs`) — and that path-only
check is what :func:`backend.vault.notes.list_notes` and ``is_indexable``
still use, deliberately: they filter by directory only, so a ``#no-ai``-tagged
note living outside ``99-Private/`` is still listed by them today. That is a
gap for RAG indexing and note listing (out of scope here — changing it would
change behaviour other callers depend on), but it is *not* a gap any new
outward-facing read endpoint should repeat. :func:`is_visible` is the full
check — directory *and* tag — for exactly that reason.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import frontmatter

from backend.core.taxonomy import Taxonomy, active_taxonomy

NO_AI_TAG = "no-ai"


def is_no_ai_text(metadata: dict, content: str) -> bool:
    """The ``#no-ai`` predicate for callers that already have the parts.

    True when the frontmatter ``tags`` list contains ``no-ai`` (with or
    without a leading ``#``) or the body mentions ``#no-ai`` anywhere.
    """
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if NO_AI_TAG in [str(tag).strip().lstrip("#") for tag in tags]:
        return True
    return f"#{NO_AI_TAG}" in content


def is_no_ai(post: frontmatter.Post) -> bool:
    """The ``#no-ai`` predicate for a parsed note."""
    return is_no_ai_text(post.metadata, post.content)


def is_private_path(rel_path: str | Path, *, taxonomy: Taxonomy | None = None) -> bool:
    """The directory half of I3: true when any path segment is a protected zone.

    Reuses :attr:`Taxonomy.excluded_top_dirs` (private/journal/dotdirs) —
    never reimplemented here.
    """
    tax = taxonomy or active_taxonomy()
    parts = PurePosixPath(Path(rel_path).as_posix()).parts
    return any(part in tax.excluded_top_dirs for part in parts)


def is_visible(
    rel_path: str | Path, post: frontmatter.Post, *, taxonomy: Taxonomy | None = None
) -> bool:
    """The full I3 check: directory **and** ``#no-ai``, together.

    This is what an outward-facing read endpoint must use — checking only one
    half (as ``is_indexable``/``list_notes`` do, for their own historical
    reasons) misses a ``#no-ai``-tagged note living outside ``99-Private/``.
    """
    if is_private_path(rel_path, taxonomy=taxonomy):
        return False
    return not is_no_ai(post)
