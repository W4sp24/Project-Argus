"""Hybrid retrieval: vector + BM25 -> RRF -> recency boost -> link expansion.

Daily-life context favors this week over last year, so chunks from the
taxonomy's daily and inbox dirs decay with age. Retrieved chunks that link to
other notes pull along one hop of context (the linked note's title line).
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.index import VaultIndex, _tokenize
from backend.vault.links import LinkIndex, build_link_index

RRF_K = 60
POOL_SIZE = 20
RECENCY_TAU_DAYS = 45.0

# Deprecated for 0.3 — bound to Taxonomy()'s defaults; prefer
# settings.taxonomy.recency_dirs / active_taxonomy().recency_dirs, which is
# what the actual recency-boost call site below uses.
RECENCY_HALF_DIRS = Taxonomy().recency_dirs


def _recency_multiplier(meta: dict, today: date, recency_dirs: tuple[str, ...]) -> float:
    path = str(meta.get("path", ""))
    if not path.startswith(recency_dirs):
        return 1.0
    raw_date = str(meta.get("date") or "")
    try:
        age_days = (today - date.fromisoformat(raw_date)).days
    except ValueError:
        return 1.0
    return math.exp(-max(age_days, 0) / RECENCY_TAU_DAYS)


def _normalize_tag(tag: str) -> str:
    """Case/whitespace-fold a tag for comparison; leading ``#`` is optional."""
    return tag.strip().lstrip("#").strip().lower()


def _tag_matches(query_tag: str, chunk_tag: str) -> bool:
    """A parent tag (``project``) matches itself and any nested child
    (``project/argus``), case-insensitively — Obsidian's own nested-tag
    convention treats a parent tag as covering its children."""
    query = _normalize_tag(query_tag)
    chunk = _normalize_tag(chunk_tag)
    if not query or not chunk:
        return False
    return chunk == query or chunk.startswith(f"{query}/")


def _passes_filters(meta: dict, course: str | None, tags: list[str] | None) -> bool:
    if course and meta.get("course") != course:
        return False
    if tags:
        chunk_tags = [t for t in str(meta.get("tags", "")).split(",") if t.strip()]
        matched = any(
            _tag_matches(query_tag, chunk_tag) for query_tag in tags for chunk_tag in chunk_tags
        )
        if not matched:
            return False
    return True


def _expand_wikilinks(
    hits: list[dict], vault_path: Path, taxonomy: Taxonomy, link_index: LinkIndex
) -> list[dict]:
    """Append one-hop context: the title line of each linked note.

    ``link_index`` is built once per :func:`retrieve` call (see below), not
    once per link — resolving against it is an in-memory dict lookup instead
    of the ``rglob()`` full-vault walk this used to do per link, per query.
    """
    seen: set[str] = set()
    extras: list[dict] = []
    for hit in hits:
        from_path = str(hit["meta"].get("path", ""))
        for link in str(hit["meta"].get("wikilinks", "")).split(","):
            name = link.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rel = link_index.resolve(name, from_path=from_path)
            if not rel:
                continue
            candidate = vault_path / rel
            if not candidate.is_file():
                continue
            first_line = ""
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip() and not line.startswith("---"):
                    first_line = line.strip()
                    break
            if first_line:
                extras.append(
                    {
                        "text": f"[[{name}]]: {first_line}",
                        "meta": {"path": rel, "title": name, "linked": True},
                        "score": 0.0,
                    }
                )
    return hits + extras


def _bm25_for(index: VaultIndex, corpus: list[dict]) -> Any:
    """The index's cached BM25 if it has one, else a throwaway built here.

    ``retrieve`` is duck-typed over the index on purpose — the whole test
    suite, the chat tool and the MCP bridge pass stand-ins that implement only
    ``query``/``all_chunks``. Calling ``index.bm25()`` unconditionally would
    make the cache an interface requirement and break every one of them, so
    the cache is an optimisation the real :class:`VaultIndex` opts into.
    """
    cached = getattr(index, "bm25", None)
    if callable(cached):
        return cached()
    if not corpus:
        return None
    from rank_bm25 import BM25Okapi

    return BM25Okapi([_tokenize(chunk["text"]) for chunk in corpus])


def retrieve(
    index: VaultIndex,
    query: str,
    vault_path: Path,
    k: int = 8,
    course: str | None = None,
    tags: list[str] | None = None,
    expand_links: bool = True,
    today: date | None = None,
    *,
    taxonomy: Taxonomy | None = None,
) -> list[dict]:
    """Top-k chunks for a query: [{text, meta, score}], best first."""
    tax = taxonomy or active_taxonomy()
    today = today or date.today()

    vector_hits = index.query(query, n_results=POOL_SIZE)

    # index.all_chunks() / index.bm25() are memoised on the VaultIndex itself
    # (invalidated by upsert/delete/reindex) — a real vault's chunk set no
    # longer gets re-fetched from chroma and re-tokenized on every query.
    corpus = index.all_chunks()
    bm25_hits: list[dict] = []
    bm25 = _bm25_for(index, corpus)
    if corpus and bm25 is not None:
        scores = bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)[:POOL_SIZE]
        bm25_hits = [corpus[i] for i in ranked if scores[i] > 0]

    # Reciprocal-rank fusion keyed by (path, text-hash).
    fused: dict[tuple, dict] = {}
    for hits in (vector_hits, bm25_hits):
        for rank, hit in enumerate(hits):
            key = (hit["meta"].get("path"), hash(hit["text"]))
            entry = fused.setdefault(key, {"hit": hit, "rrf": 0.0})
            entry["rrf"] += 1.0 / (RRF_K + rank + 1)

    scored: list[dict] = []
    for entry in fused.values():
        hit, rrf = entry["hit"], entry["rrf"]
        if not _passes_filters(hit["meta"], course, tags):
            continue
        scored.append(
            {
                "text": hit["text"],
                "meta": hit["meta"],
                "score": rrf * _recency_multiplier(hit["meta"], today, tax.recency_dirs),
            }
        )
    scored.sort(key=lambda hit: hit["score"], reverse=True)
    top = scored[:k]

    if not expand_links:
        return top
    # Built once per retrieve() call, not once per link (see module docstring
    # on backend/vault/links.py for why a per-link rglob() was a latency bug).
    link_index = build_link_index(vault_path, taxonomy=tax)
    return _expand_wikilinks(top, vault_path, tax, link_index)
