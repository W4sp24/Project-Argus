"""Argus FastAPI application.

REST endpoints + the /ws/chat WebSocket for the dashboard. CORS is restricted
to the local Next.js dev server. Run with ``uvicorn backend.main:app --port 8000``.
"""

from __future__ import annotations

import os
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

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# The desktop shell serves Next on a dynamically-allocated port, so it passes
# its exact origin through ARGUS_ALLOWED_ORIGINS (comma-separated). Never "*":
# the vault is readable through these routes.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ARGUS_ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)
    ).split(",")
    if origin.strip()
]


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str = "ok"


class VaultInfo(BaseModel):
    """Vault identity for building obsidian:// deep links client-side."""

    name: str


ChatRunner = Callable[[str], AsyncIterator[str]]


def _default_chat_runner(settings: Settings) -> ChatRunner:
    """Lazily build the real agent so the app boots without agent deps."""
    import threading

    from backend.agent.runtime import ChatAgent

    agent = ChatAgent(settings)
    threading.Thread(target=agent.warm, daemon=True).start()
    return agent.stream_chat


def create_app(
    settings: Settings | None = None,
    chat_runner: ChatRunner | None = None,
    generator: Callable | None = None,
    index_factory: Callable | None = None,
    planner: Callable | None = None,
    briefing_composer: Callable | None = None,
    scheduler_factory: Callable | None = None,
) -> FastAPI:
    """Build the FastAPI app around the given (or default) settings.

    ``chat_runner``, ``generator``, ``index_factory``, ``planner``, and
    ``briefing_composer`` are injectable so tests run with fakes instead of
    the agent SDK / embedding model. ``scheduler_factory`` is only passed by
    the module-level app below — test apps never start background threads.
    """
    from contextlib import asynccontextmanager

    resolved = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler = scheduler_factory(resolved) if scheduler_factory else None
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Argus", version="0.1.0", lifespan=lifespan)
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

    @app.get("/api/vault", response_model=VaultInfo)
    def vault_info() -> VaultInfo:
        return VaultInfo(name=resolved.vault_path.name)

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

    def _default_index_factory() -> object:
        from backend.rag.index import VaultIndex

        return VaultIndex(resolved.db_path.parent / "chroma")

    def _default_generator(feature: str) -> Callable:
        """agent_generate bound to a feature label + db so usage rows attribute."""
        from backend.agent.generate import agent_generate

        def _generate(prompt: str):
            return agent_generate(prompt, feature=feature, db_path=resolved.db_path)

        return _generate

    from backend.study.api import build_study_router

    app.include_router(
        build_study_router(
            resolved,
            generator or _default_generator("study"),
            index_factory or _default_index_factory,
        )
    )

    from backend.ingest_api import build_ingest_router

    app.include_router(
        build_ingest_router(
            resolved,
            generator or _default_generator("ingest"),
            index_factory or _default_index_factory,
        )
    )

    from backend.system_api import build_system_router

    app.include_router(build_system_router(resolved))

    from backend.tasks.api import build_tasks_router

    app.include_router(build_tasks_router(resolved))

    from backend.notes_api import build_notes_router

    app.include_router(build_notes_router(resolved))

    from backend.flashcards_api import build_flashcards_router

    app.include_router(build_flashcards_router(resolved))

    from backend.search_api import build_search_router

    app.include_router(
        build_search_router(resolved, index_factory or _default_index_factory)
    )

    def _default_planner():
        from backend.agent.planner import run_planner

        return run_planner

    from backend.review_api import build_review_router

    app.include_router(build_review_router(resolved, planner or _default_planner()))

    def _default_composer() -> Callable:
        from backend.briefing import agent_composer

        return agent_composer

    from backend.briefing_api import build_briefing_router

    app.include_router(build_briefing_router(resolved, briefing_composer or _default_composer()))

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        """Bridge agent streaming deltas to the browser.

        Frames in: {message, model?} — ``model`` (a registry name, §7) is
        optional and flows through to runners that accept it; runners with
        the legacy single-argument signature keep working.
        Frames out: {type: "delta", text} ... {type: "done"} | {type: "error", detail}.
        """
        await websocket.accept()
        runner = chat_runner or _default_chat_runner(resolved)
        try:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                model = str(payload.get("model") or "").strip() or None
                if not message:
                    await websocket.send_json({"type": "error", "detail": "empty message"})
                    continue
                try:
                    if model is not None:
                        try:
                            stream = runner(message, model)
                        except TypeError:  # injected runner without model support
                            stream = runner(message)
                    else:
                        stream = runner(message)
                    async for delta in stream:
                        await websocket.send_json({"type": "delta", "text": delta})
                    await websocket.send_json({"type": "done"})
                except Exception as exc:  # agent errors must reach the UI, not kill the socket
                    await websocket.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return

    return app


def _production_scheduler(settings: Settings):
    from backend.briefing import agent_composer
    from backend.scheduler import build_scheduler

    return build_scheduler(settings, composer=agent_composer)


app = create_app(scheduler_factory=_production_scheduler)
