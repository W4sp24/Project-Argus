"""Dev-journal endpoints — read-only by contract (D1): no write routes exist.

The journal zone (``90-Meta/``) is dev-owned: Claude Code writes it, Argus only
reads it back for the dashboard's Journal page.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.core.config import Settings
from backend.vault.journal import (
    JournalNote,
    JournalPathError,
    JournalProject,
    JournalSession,
    list_projects,
    list_sessions,
    read_note,
)


def build_journal_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/journal")

    @router.get("/projects", response_model=list[JournalProject])
    def journal_projects() -> list[JournalProject]:
        return list_projects(settings.vault_path)

    @router.get("/sessions", response_model=list[JournalSession])
    def journal_sessions(project: str | None = None) -> list[JournalSession]:
        return list_sessions(settings.vault_path, project)

    @router.get("/note", response_model=JournalNote)
    def journal_note(path: str) -> JournalNote:
        try:
            note = read_note(settings.vault_path, path)
        except JournalPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        return note

    return router
