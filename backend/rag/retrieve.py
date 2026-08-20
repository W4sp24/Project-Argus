"""Hybrid retrieval: vector + BM25 -> RRF -> recency boost -> link expansion.

Daily-life context favors this week over last year, so chunks from the
taxonomy's daily and inbox dirs decay with age. Retrieved chunks that link to
other notes pull along one hop of context (the linked note's title line).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.index import VaultIndex, _tokenize
from backend.vault.links import LinkIndex, build_link_index

# --- Tuning constants --------------------------------------------------------
# Every knob retrieve() leans on lives here so a future tune touches one block
# instead of hunting through the pipeline.

# Reciprocal-rank-fusion damping (the constant from the original RRF paper).
# Higher flattens the weighting across ranks (rank 1 and rank 5 fuse closer
# together); lower makes the top rank dominate fusion more. Changing it
# reshuffles which of two close-but-not-identical hits wins.
RRF_K = 60

# How many candidates each of vector/BM25 fetches before fusion. Too small
# starves a filtered query (a course= query whose global top-N is all other
# courses returns near nothing even though matching chunks exist) — that
# starvation is exactly why filters are now pushed into the fetch instead of
# applied after this cut. Too large makes every query pay to score chunks
# that can never survive filtering.
POOL_SIZE = 20

# Exponential recency decay time constant, in days. Raised 45 -> 120: 45 was
# aggressive enough to erase (not just demote) anything past ~3 months, which
# buried a genuinely relevant but slightly stale note under raw freshness.
RECENCY_TAU_DAYS = 120.0

# Floor on the recency decay multiplier. Without it, exp(-age/tau) asymptotes
# toward zero and an old-but-highly-relevant note becomes unreachable no
# matter how strong the match — recency should demote, never delete.
RECENCY_FLOOR = 0.15

# Multiplier applied to an undated (or malformed-date) note in a recency dir,
# in place of the 1.0 "no penalty" that used to leak through the old
# `except ValueError: return 1.0` path. That leak let an undated daily note
# outrank every dated one; 0.5 keeps it retrievable but not privileged.
RECENCY_UNDATED_MULTIPLIER = 0.5

# A vector hit's raw cosine similarity must clear this to survive into the
# result set, unless it's also a strong BM25 match (see BM25_STRONG_RANK).
# With bge-small-en-v1.5 under cosine distance, unrelated text pairs typically
# land ~0.2-0.5 and genuine semantic matches ~0.55+ — 0.5 is picked
# conservatively so paraphrase-y real matches survive while a nonsense
# query's whole pool (all noise) gets rejected instead of dressed up as k
# confident-looking results.
MIN_SIMILARITY = 0.5

# A hit ranked within this many BM25 places (with a positive BM25 score)
# survives MIN_SIMILARITY even if its vector similarity is low or absent —
# a rare exact-term hit the embedder missed must not be silently dropped.
BM25_STRONG_RANK = 5

# Cap on how many one-hop link expansions retrieve_result() appends to
# `.related`. Unbounded expansion used to silently break the "k means k"
# contract every caller (search API, chat tool, MCP bridge) depends on.
MAX_LINK_EXPANSIONS = 8


def _recency_multiplier(meta: dict, today: date, recency_dirs: tuple[str, ...]) -> float:
    path = str(meta.get("path", ""))
    if not path.startswith(recency_dirs):
        return 1.0
    raw_date = meta.get("date")
    # meta["date"] is normally "" or an ISO date string, but a stand-in index
    # (or a future chunker bug) could hand back a non-string; isinstance
    # avoids the TypeError date.fromisoformat() would otherwise raise on it.
    if not isinstance(raw_date, str) or not raw_date:
        return RECENCY_UNDATED_MULTIPLIER
    try:
        age_days = (today - date.fromisoformat(raw_date)).days
    except ValueError:
        return RECENCY_UNDATED_MULTIPLIER
    decay = math.exp(-max(age_days, 0) / RECENCY_TAU_DAYS)
    return max(decay, RECENCY_FLOOR)


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
    """Tag filtering (chroma can't express Obsidian's nested-tag semantics
    over the comma-joined ``tags`` string, so it always happens here) plus a
    belt-and-braces course re-check for pools a stand-in index didn't filter
    at the source."""
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


def _vector_query(
    index: VaultIndex, query: str, n_results: int, where: dict | None
) -> list[dict]:
    """Vector search, honoring an optional metadata filter when the index supports one.

    ``where=`` is what fixes filter starvation (a course= query used to get
    filtered only after its pool was already cut to POOL_SIZE), but retrieve()
    is duck-typed over the index — stand-ins (tests, the chat tool, the MCP
    bridge) may implement ``query(text, n_results)`` without a ``where``
    parameter at all. Retry without it rather than making ``where`` a required
    part of the interface.
    """
    if where:
        try:
            return index.query(query, n_results=n_results, where=where)
        except TypeError:
            pass
    return index.query(query, n_results=n_results)


def _bm25_hits_for_course(
    corpus: list[dict], bm25: Any, query: str, pool_size: int, course: str | None
) -> list[dict]:
    """BM25 candidates, course-filtered *before* the pool is truncated.

    Scoring stays against the full (unfiltered) ``corpus`` — that's the list
    the index's memoised BM25Okapi was actually built over, real or
    stand-in — so ``scores[i]`` and ``corpus[i]`` never drift apart. The
    course predicate is applied while walking the already-ranked indices, so
    a course whose matches all sit outside the first POOL_SIZE globally-ranked
    slots still gets its own POOL_SIZE-worth of candidates, instead of being
    filtered away after truncation already discarded them (the starvation
    bug this module exists to fix).
    """
    if not corpus or bm25 is None:
        return []
    scores = bm25.get_scores(_tokenize(query))
    ranked_idx = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
    hits: list[dict] = []
    for i in ranked_idx:
        if scores[i] <= 0:
            break  # descending order: nothing further can score positively
        chunk = corpus[i]
        if course and chunk["meta"].get("course") != course:
            continue
        hits.append(chunk)
        if len(hits) >= pool_size:
            break
    return hits


def _expand_wikilinks(hits: list[dict], vault_path: Path, link_index: LinkIndex) -> list[dict]:
    """One-hop context for ``hits``: the title line of each linked note.

    ``link_index`` is built (or fetched from cache) once per :func:`retrieve_result`
    call, not once per link — resolving against it is an in-memory dict lookup
    instead of the ``rglob()`` full-vault walk this used to do per link, per query.
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
                        "meta": {"path": rel, "title": name, "linked": True, "kind": "link"},
                        "score": 0.0,
                    }
                )
    return extras


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


def _link_index_for(index: VaultIndex, vault_path: Path, tax: Taxonomy) -> LinkIndex:
    """The index's cached link index if it has one, else build one here.

    Same duck-typing rationale as :func:`_bm25_for`: a stand-in implementing
    only ``query``/``all_chunks`` (tests, the chat tool, the MCP bridge) must
    keep working, so the memoised ``link_index()`` method on the real
    :class:`VaultIndex` is an optimisation it opts into, not a required part
    of the interface every index-like object must implement.
    """
    cached = getattr(index, "link_index", None)
    if callable(cached):
        return cached(vault_path, taxonomy=tax)
    return build_link_index(vault_path, taxonomy=tax)


@dataclass(frozen=True)
class RetrievalResult:
    """``results`` is at most ``k`` real matches; ``related`` is one-hop link
    context, capped at :data:`MAX_LINK_EXPANSIONS` and never counted against ``k``."""

    results: list[dict]
    related: list[dict]


def retrieve_result(
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
    rerank: bool = False,
) -> RetrievalResult:
    """Top-k chunks for a query, plus bounded one-hop link context.

    ``results`` entries are ``{text, meta, score, similarity}``, best first —
    ``score`` is the fused (RRF x recency) rank score, ``similarity`` is the
    raw cosine similarity from the vector search (``None`` for a BM25-only
    hit). Returns an empty ``results`` list when nothing clears the
    similarity floor (see ``MIN_SIMILARITY``) rather than a pool of
    plausible-looking but unrelated chunks.
    """
    tax = taxonomy or active_taxonomy()
    today = today or date.today()

    # chroma can't express Obsidian's nested-tag semantics over the
    # comma-joined "tags" metadata string, so tag filtering always happens
    # post-fusion in _passes_filters. Over-fetch the pool so that post-filter
    # has enough material to work with instead of starving the same way an
    # unfiltered course query used to.
    pool_size = POOL_SIZE * 3 if tags else POOL_SIZE

    where = {"course": course} if course else None
    vector_hits = _vector_query(index, query, pool_size, where)
    similarity_by_key = {
        (hit["meta"].get("path"), hash(hit["text"])): hit.get("score") for hit in vector_hits
    }

    # index.all_chunks() / index.bm25() are memoised on the VaultIndex itself
    # (invalidated by upsert/delete/reindex) — a real vault's chunk set no
    # longer gets re-fetched from chroma and re-tokenized on every query.
    corpus = index.all_chunks()
    bm25 = _bm25_for(index, corpus)
    bm25_hits = _bm25_hits_for_course(corpus, bm25, query, pool_size, course)
    bm25_rank = {}
    for rank, hit in enumerate(bm25_hits):
        key = (hit["meta"].get("path"), hash(hit["text"]))
        bm25_rank.setdefault(key, rank)

    # Reciprocal-rank fusion keyed by (path, text-hash).
    fused: dict[tuple, dict] = {}
    for hits in (vector_hits, bm25_hits):
        for rank, hit in enumerate(hits):
            key = (hit["meta"].get("path"), hash(hit["text"]))
            entry = fused.setdefault(key, {"hit": hit, "rrf": 0.0})
            entry["rrf"] += 1.0 / (RRF_K + rank + 1)

    scored: list[dict] = []
    for key, entry in fused.items():
        hit, rrf = entry["hit"], entry["rrf"]
        if not _passes_filters(hit["meta"], course, tags):
            continue
        similarity = similarity_by_key.get(key)
        strongly_bm25 = key in bm25_rank and bm25_rank[key] < BM25_STRONG_RANK
        if similarity is not None and similarity < MIN_SIMILARITY and not strongly_bm25:
            continue
        scored.append(
            {
                "text": hit["text"],
                "meta": hit["meta"],
                "score": rrf * _recency_multiplier(hit["meta"], today, tax.recency_dirs),
                "similarity": similarity,
            }
        )
    scored.sort(key=lambda hit: hit["score"], reverse=True)

    if rerank:
        from backend.rag.rerank import rerank as _rerank

        top = _rerank(query, scored, k)
    else:
        top = scored[:k]

    if not expand_links or not top:
        return RetrievalResult(results=top, related=[])

    # Skip build_link_index entirely (a full vault rglob() + per-note
    # frontmatter parse) when nothing in the top-k even has a wikilink to
    # resolve — the common case, and the cheapest of the two latency fixes.
    if not any(str(hit["meta"].get("wikilinks", "")).strip() for hit in top):
        return RetrievalResult(results=top, related=[])

    link_index = _link_index_for(index, vault_path, tax)
    related = _expand_wikilinks(top, vault_path, link_index)[:MAX_LINK_EXPANSIONS]
    return RetrievalResult(results=top, related=related)


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
    rerank: bool = False,
) -> list[dict]:
    """Thin shim over :func:`retrieve_result` for existing callers.

    ``backend.features.search.router`` and ``backend.agent.runtime``'s
    ``search_vault`` tool both call this directly and don't distinguish real
    hits from link expansions, so this keeps returning ``results + related``
    exactly as before — a caller that wants the k-bounded/related split
    should move to :func:`retrieve_result` (planned for a follow-up commit).

    ``rerank`` is forwarded rather than dropped. It was not, and since both
    callers use this shim, ``Settings.rerank_enabled`` reached nothing:
    ``ARGUS_RAG_RERANK=1`` did exactly nothing while ``rerank.py``'s docstring
    said the setting gated it.
    """
    result = retrieve_result(
        index,
        query,
        vault_path,
        k=k,
        course=course,
        tags=tags,
        expand_links=expand_links,
        today=today,
        taxonomy=taxonomy,
        rerank=rerank,
    )
    return result.results + result.related
