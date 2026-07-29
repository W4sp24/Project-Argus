"""Mapping :mod:`backend.vault.writer` failures onto HTTP status codes.

Extracted from the notes router because the task-line endpoints raise exactly
the same ``WriterError`` hierarchy and must answer with the same codes. Two
routers now share one mapping rather than drifting apart.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from backend.vault.writer import (
    WriterConflict,
    WriterError,
    WriterExists,
    WriterForbidden,
    WriterMissing,
)


def raise_http(exc: WriterError, current_content: str | None = None) -> NoReturn:
    """Re-raise a writer failure as the HTTPException the dashboard expects.

    ``current_content`` is only meaningful for :class:`WriterConflict`, where
    the client needs the on-disk text to resolve the conflict.
    """
    if isinstance(exc, WriterForbidden):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, WriterMissing):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, WriterExists):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, WriterConflict):
        detail: object = {"message": str(exc), "current_content": current_content}
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc
