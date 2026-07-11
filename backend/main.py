"""FRIDAY FastAPI application.

REST endpoints for the dashboard. CORS is restricted to the local Next.js dev
server. Run with ``uvicorn backend.main:app --port 8000``.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import ConfigError, Settings
from backend.journal import (
    JournalNote,
    JournalPathError,
    JournalProject,
    JournalSession,
    list_projects,
    list_sessions,
    read_note,
)
from backend.notes import NoteInfo, list_notes

ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app around the given (or default) settings."""
    resolved = settings or Settings.load()
    app = FastAPI(title="FRIDAY", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ConfigError)
    def config_error(_request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/notes", response_model=list[NoteInfo])
    def notes() -> list[NoteInfo]:
        return list_notes(resolved.vault_path)

    # Dev journal — read-only by contract (D1): no write endpoints exist.

    @app.get("/api/journal/projects", response_model=list[JournalProject])
    def journal_projects() -> list[JournalProject]:
        return list_projects(resolved.vault_path)

    @app.get("/api/journal/sessions", response_model=list[JournalSession])
    def journal_sessions(project: str | None = None) -> list[JournalSession]:
        return list_sessions(resolved.vault_path, project)

    @app.get("/api/journal/note", response_model=JournalNote)
    def journal_note(path: str) -> JournalNote:
        try:
            note = read_note(resolved.vault_path, path)
        except JournalPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        return note

    return app


app = create_app()
