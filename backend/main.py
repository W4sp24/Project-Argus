"""Argus FastAPI application — the composition root.

Builds the app and mounts one router per feature. No route is defined here
except ``/health``: everything else lives in ``backend.features.<name>.router``.
Run with ``uvicorn backend.main:app --port 8000``.

Router modules are imported at module scope deliberately. None of them pulls a
heavy optional dependency at import time — the ``[rag]`` stack is deferred
inside :mod:`backend.rag.index`'s methods and the agent SDK inside
:func:`backend.features.chat.router.default_chat_runner` — so the app still
boots with neither extra installed.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.config import ConfigError, Settings
from backend.features.briefing.router import build_briefing_router
from backend.features.chat.router import ChatRunner, build_chat_router
from backend.features.flashcards.router import build_flashcards_router
from backend.features.ingest.router import build_ingest_router
from backend.features.insights.router import build_insights_router
from backend.features.journal.router import build_journal_router
from backend.features.notes.router import build_notes_router
from backend.features.quick_links.router import build_quick_links_router
from backend.features.review.router import build_review_router
from backend.features.search.router import build_search_router
from backend.features.study.router import build_study_router
from backend.features.system.router import build_system_router
from backend.features.tasks.router import build_tasks_router

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# The desktop shell serves Next on a dynamically-allocated port, so it passes
# its exact origin through ARGUS_ALLOWED_ORIGINS (comma-separated). Never "*":
# the vault is readable through these routes.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ARGUS_ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)).split(
        ","
    )
    if origin.strip()
]


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str = "ok"


def create_app(
    settings: Settings | None = None,
    chat_runner: ChatRunner | None = None,
    generator: Callable | None = None,
    index_factory: Callable | None = None,
    planner: Callable | None = None,
    briefing_composer: Callable | None = None,
    scheduler_factory: Callable | None = None,
    model_prober: Callable | None = None,
    model_puller: Callable | None = None,
) -> FastAPI:
    """Build the FastAPI app around the given (or default) settings.

    ``chat_runner``, ``generator``, ``index_factory``, ``planner``,
    ``briefing_composer``, ``model_prober`` and ``model_puller`` are injectable
    so tests run with fakes instead of the agent SDK / embedding model / a live
    provider. ``scheduler_factory`` is only passed by the module-level app
    below — test apps never start background threads.
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

    def _default_index_factory() -> object:
        from backend.rag.index import VaultIndex

        return VaultIndex(resolved.db_path.parent / "chroma", taxonomy=resolved.taxonomy)

    def _default_generator(feature: str) -> Callable:
        """agent_generate bound to a feature label + db so usage rows attribute.

        ``model`` is optional and names a registry entry (§7); omitting it
        keeps the historical default backend.
        """
        from backend.agent.generate import agent_generate

        def _generate(prompt: str, model: str | None = None):
            return agent_generate(
                prompt,
                feature=feature,
                db_path=resolved.db_path,
                model=model,
                settings=resolved,
            )

        return _generate

    def _default_planner():
        from backend.agent.planner import run_planner

        return run_planner

    def _default_composer() -> Callable:
        from backend.features.briefing.service import make_agent_composer

        return make_agent_composer(resolved)

    index = index_factory or _default_index_factory

    app.include_router(build_notes_router(resolved))
    app.include_router(build_journal_router(resolved))
    app.include_router(
        build_study_router(resolved, generator or _default_generator("study"), index)
    )
    app.include_router(
        build_ingest_router(resolved, generator or _default_generator("ingest"), index)
    )
    app.include_router(build_system_router(resolved, model_prober, model_puller))
    app.include_router(build_tasks_router(resolved))
    app.include_router(build_flashcards_router(resolved))
    app.include_router(build_quick_links_router(resolved))
    app.include_router(build_search_router(resolved, index))
    app.include_router(build_review_router(resolved, planner or _default_planner()))
    app.include_router(build_briefing_router(resolved, briefing_composer or _default_composer()))
    app.include_router(build_insights_router(resolved))
    app.include_router(build_chat_router(resolved, chat_runner))

    return app


def _production_scheduler(settings: Settings):
    from backend.features.briefing.service import make_agent_composer
    from backend.scheduler import build_scheduler

    return build_scheduler(settings, composer=make_agent_composer(settings))


app = create_app(scheduler_factory=_production_scheduler)
