"""FRIDAY FastAPI application.

REST endpoints + the /ws/chat WebSocket for the dashboard. CORS is restricted
to the local Next.js dev server. Run with ``uvicorn backend.main:app --port 8000``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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


ChatRunner = Callable[[str], AsyncIterator[str]]


def _default_chat_runner(settings: Settings) -> ChatRunner:
    """Lazily build the real agent so the app boots without agent deps."""
    from backend.agent.runtime import ChatAgent

    return ChatAgent(settings).stream_chat


def create_app(settings: Settings | None = None, chat_runner: ChatRunner | None = None) -> FastAPI:
    """Build the FastAPI app around the given (or default) settings.

    ``chat_runner`` is injectable so tests can stream canned deltas without
    touching the real agent SDK.
    """
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

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        """Bridge agent streaming deltas to the browser.

        Frames out: {type: "delta", text} ... {type: "done"} | {type: "error", detail}.
        """
        await websocket.accept()
        runner = chat_runner or _default_chat_runner(resolved)
        try:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                if not message:
                    await websocket.send_json({"type": "error", "detail": "empty message"})
                    continue
                try:
                    async for delta in runner(message):
                        await websocket.send_json({"type": "delta", "text": delta})
                    await websocket.send_json({"type": "done"})
                except Exception as exc:  # agent errors must reach the UI, not kill the socket
                    await websocket.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return

    return app


app = create_app()
