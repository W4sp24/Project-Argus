"""Which existing notes a new note is nearest to.

Split from :mod:`backend.vault.relations` for a layering reason rather than a
stylistic one: :mod:`backend.rag.retrieve` imports
:mod:`backend.vault.links`, so ``vault`` importing ``retrieve`` back would be
a cycle. The pure half stays in ``vault/``; this is the half that needs the
index, and ``features/`` composes the two.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy
from backend.rag.retrieve import retrieve_result
from backend.vault.relations import MAX_NEIGHBOURS

logger = logging.getLogger("argus.rag")

#: How much of a note is used as the neighbour query. The whole note would
#: blur the query toward its average subject; the opening is where a note says
#: what it is about, which is the question being asked.
NEIGHBOUR_QUERY_CHARS = 1500


def nearest_notes(
    index: Any,
    vault_path: Path,
    text: str,
    *,
    exclude: Iterable[str],
    limit: int = MAX_NEIGHBOURS,
    taxonomy: Taxonomy | None = None,
) -> list[tuple[str, str]]:
    """``(rel_path, title)`` for the nearest distinct notes, best first.

    ``expand_links=False`` because the question is "what else in the vault is
    about this", not "what do those things link to" -- one hop out is already
    what the reader gets from the links themselves, and letting retrieval add
    another would put notes in the list that match nothing in this one.

    Deduplicated by path: a strong match usually contributes several chunks,
    and three chunks of one note is not three neighbours.

    **Never raises.** A note whose neighbours could not be computed is still a
    good note, and losing it because chroma is unavailable is not a trade
    worth making -- the same reasoning that already governs how
    ``ingest.pipeline._run_one`` treats a dead generator.
    """
    body = (text or "").strip()[:NEIGHBOUR_QUERY_CHARS]
    if not body:
        return []
    skip = set(exclude)
    try:
        result = retrieve_result(
            index,
            body,
            vault_path,
            k=8,
            expand_links=False,
            taxonomy=taxonomy,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("neighbours unavailable: %s", exc)
        return []

    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hit in result.results:
        meta = hit.get("meta") or {}
        rel_path = str(meta.get("path") or "")
        if not rel_path or rel_path in skip or rel_path in seen:
            continue
        seen.add(rel_path)
        found.append((rel_path, str(meta.get("title") or rel_path.rsplit("/", 1)[-1])))
        if len(found) == limit:
            break
    return found
