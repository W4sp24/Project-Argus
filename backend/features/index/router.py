"""Manual vault reindex + status (Argus v0.2.1).

Before this existed, the only way to fill the chroma collection was the
CLI-only ``argus reindex`` — nothing in the shipped app ever ran it, so a
freshly-ingested or freshly-installed vault searched and chatted against an
empty index with no visible error (``VaultIndex.query`` returns ``[]`` on an
empty collection; see :mod:`backend.rag.index`). This router is the piece that
lets the dashboard actually trigger and observe a rebuild.

Reindexing loads the embedding model and walks the whole vault, so it must
never run on the request thread — modeled on
:mod:`backend.features.study.router`'s upload-then-background-index pattern.
A module-level lock (rather than one scoped to a single router instance)
keeps two overlapping triggers — a double-click, or the boot-time auto-index
in :func:`backend.main.create_app`'s lifespan racing a user-triggered POST —
from starting two rebuilds against the same chroma directory at once.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.config import Settings

logger = logging.getLogger("argus.rag")

_lock = threading.Lock()


class _State:
    """In-process reindex status. One Argus backend process, one of these."""

    def __init__(self) -> None:
        self.indexing = False
        self.last_run: str | None = None
        self.last_error: str | None = None


_state = _State()


class IndexStatus(BaseModel):
    """``GET /api/index/status`` and the immediate reply to a reindex trigger."""

    chunks: int
    files: int
    indexing: bool
    last_run: str | None = None
    last_error: str | None = None
    stale: bool = False


def _run_reindex(settings: Settings, index_factory: Any) -> None:
    """The background job body. Never raises — failures land in ``last_error``.

    Runs on a daemon thread started by the request handler (or by the app's
    boot-time auto-index), so an exception escaping here would only ever be
    seen in a log nobody is watching; catching it and recording it in
    ``_state`` is what makes a broken index visible instead of just quiet.
    """
    try:
        index = index_factory()
        result = index.reindex_all(settings.vault_path)
        if result.errors:
            _state.last_error = "; ".join(
                f"{path}: {message}" for path, message in result.errors.items()
            )
            logger.warning(
                "reindex finished with %d error(s): %s", len(result.errors), _state.last_error
            )
        else:
            _state.last_error = None
            logger.info(
                "reindex complete: %d chunks from %d files", result.total_chunks, result.files
            )
    except Exception as exc:  # a genuinely broken index must be visible, not silently dropped
        logger.exception("reindex failed")
        _state.last_error = str(exc)
    finally:
        _state.last_run = datetime.now(UTC).isoformat()
        _state.indexing = False


def _status(index_factory: Any) -> IndexStatus:
    chunks = files = 0
    stale = False
    try:
        index = index_factory()
        size = index.size()
        chunks, files = size["chunks"], size["files"]
        stale = index.schema_stale()
    except Exception as exc:  # index truly unavailable — report it, don't 500
        logger.warning("index status unavailable: %s", exc)
    return IndexStatus(
        chunks=chunks,
        files=files,
        indexing=_state.indexing,
        last_run=_state.last_run,
        last_error=_state.last_error,
        stale=stale,
    )


def build_index_router(settings: Settings, index_factory: Any) -> APIRouter:
    """``/api/index`` routes. ``index_factory`` is injectable (tests use a fake)."""
    router = APIRouter(prefix="/api/index")

    @router.post("/reindex", status_code=202, response_model=IndexStatus)
    def reindex() -> IndexStatus:
        with _lock:
            if not _state.indexing:
                _state.indexing = True
                threading.Thread(
                    target=_run_reindex, args=(settings, index_factory), daemon=True
                ).start()
        return _status(index_factory)

    @router.get("/status", response_model=IndexStatus)
    def status() -> IndexStatus:
        return _status(index_factory)

    return router
