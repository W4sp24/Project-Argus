"""Standalone semantic search (redesign command palette): fast, non-agentic
vector+BM25 retrieval over the vault — citations only, no generated answer.

This is the same :func:`backend.rag.retrieve.retrieve` call that
``backend.agent.runtime``'s ``search_vault`` tool makes inside the chat agent
loop, exposed here as a plain synchronous HTTP route with no LLM/chat loop
involved. Keep it that way — this must stay a thin citation lookup, never a
mini chat response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import Settings

SNIPPET_CHARS = 500


class SearchResult(BaseModel):
    """One cited snippet: enough to render + open the source note."""

    snippet: str
    source_path: str
    title: str | None = None
    score: float


def build_search_router(settings: Settings, index_factory: Any) -> APIRouter:
    """``GET /api/search?q=``. ``index_factory`` is injectable (tests use a fake).

    Degrades to an empty list — never a 500 — when the query is blank or the
    vault index isn't built/available (missing [rag] extras, no vault
    configured, etc.), matching this codebase's "must work
    offline/unconfigured" convention (D-020).
    """
    router = APIRouter(prefix="/api")

    @router.get("/search", response_model=list[SearchResult])
    def search(q: str = "") -> list[SearchResult]:
        query = q.strip()
        if not query:
            return []
        try:
            from backend.rag.retrieve import retrieve

            index = index_factory()
            hits = retrieve(index, query, settings.vault_path, k=8)
        except Exception:
            return []
        return [
            SearchResult(
                snippet=str(hit["text"])[:SNIPPET_CHARS],
                source_path=str(hit["meta"].get("path", "")),
                title=hit["meta"].get("title"),
                score=float(hit["score"]),
            )
            for hit in hits
        ]

    return router
