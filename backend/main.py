"""FRIDAY FastAPI application.

REST endpoints for the dashboard. CORS is restricted to the local Next.js dev
server. Run with ``uvicorn backend.main:app --port 8000``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import ConfigError, Settings
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

    return app


app = create_app()
