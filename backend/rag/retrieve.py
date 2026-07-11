"""Hybrid retrieval: vector + BM25 -> RRF -> recency boost -> link expansion.

Daily-life context favors this week over last year, so chunks from
``10-Daily/`` and ``00-Inbox/`` decay with age. Retrieved chunks that link to
other notes pull along one hop of context (the linked note's title line).
"""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path

from backend.rag.index import VaultIndex

RRF_K = 60
POOL_SIZE = 20
RECENCY_HALF_DIRS = ("10-Daily/", "00-Inbox/")
RECENCY_TAU_DAYS = 45.0
TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _recency_multiplier(meta: dict, today: date) -> float:
    path = str(meta.get("path", ""))
    if not path.startswith(RECENCY_HALF_DIRS):
        return 1.0
    raw_date = str(meta.get("date") or "")
    try:
        age_days = (today - date.fromisoformat(raw_date)).days
    except ValueError:
        return 1.0
    return math.exp(-max(age_days, 0) / RECENCY_TAU_DAYS)


def _passes_filters(meta: dict, course: str | None, tags: list[str] | None) -> bool:
    if course and meta.get("course") != course:
        return False
    if tags:
        chunk_tags = set(str(meta.get("tags", "")).split(","))
        if not set(tags) & chunk_tags:
            return False
    return True


def _expand_wikilinks(hits: list[dict], vault_path: Path) -> list[dict]:
    """Append one-hop context: the title line of each linked note."""
    seen: set[str] = set()
    extras: list[dict] = []
    for hit in hits:
        for link in str(hit["meta"].get("wikilinks", "")).split(","):
            name = link.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            for candidate in vault_path.rglob(f"{name}.md"):
                rel = candidate.relative_to(vault_path).as_posix()
                from backend.rag.paths import is_indexable

                if not is_indexable(rel):
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
                break
    return hits + extras


def retrieve(
    index: VaultIndex,
    query: str,
    vault_path: Path,
    k: int = 8,
    course: str | None = None,
    tags: list[str] | None = None,
    expand_links: bool = True,
    today: date | None = None,
) -> list[dict]:
    """Top-k chunks for a query: [{text, meta, score}], best first."""
    today = today or date.today()

    vector_hits = index.query(query, n_results=POOL_SIZE)

    corpus = index.all_chunks()
    bm25_hits: list[dict] = []
    if corpus:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([_tokenize(chunk["text"]) for chunk in corpus])
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
                "score": rrf * _recency_multiplier(hit["meta"], today),
            }
        )
    scored.sort(key=lambda hit: hit["score"], reverse=True)
    top = scored[:k]

    return _expand_wikilinks(top, vault_path) if expand_links else top
