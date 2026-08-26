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

import logging
import os
import threading
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.config import ConfigError, Settings
from backend.features.automations.router import build_automations_router
from backend.features.briefing.router import build_briefing_router
from backend.features.chat.router import ChatRunner, build_chat_router
from backend.features.external.server import start_external_server
from backend.features.flashcards.router import build_flashcards_router
from backend.features.index.router import build_index_router
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

logger = logging.getLogger("argus.rag")

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _version() -> str:
    """The installed package version, for ``/docs`` and the OpenAPI schema.

    Read from installed metadata rather than written here as a literal: the
    hardcoded one said 0.1.0 while v0.2.0 was the shipped tag, because nothing
    kept them in sync. Falling back rather than raising keeps the app bootable
    from a source tree that was never pip-installed (some test runners).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("argus")
    except PackageNotFoundError:
        return "0.0.0+dev"

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
    ingest_job_runner: Callable | None = None,
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

    def _auto_index_and_watch(stop_event: threading.Event) -> None:
        """Background thread: index the vault if needed, then watch it live.

        This is the fix for the actual reported bug — nothing in the shipped
        app ever called ``VaultIndex.reindex_all``/``watch_vault`` before this,
        so a vault full of notes searched and chatted against an empty chroma
        collection with no visible error. Only runs when ``scheduler_factory``
        is given (see this function's caller): a test app must never spawn a
        watcher thread or load the embedding model.

        Guarded broadly: ``VAULT_PATH`` may be unset on a fresh machine
        (``ConfigError``), and the ``[rag]`` extras may not be installed at all
        (``ImportError`` from inside ``VaultIndex``) — neither may crash the app.
        """
        try:
            vault_path = resolved.vault_path
        except ConfigError:
            return
        try:
            from backend.rag.watcher import watch_vault

            vault_index = index()
            if vault_index.collection.count() == 0 or vault_index.schema_stale():
                vault_index.reindex_all(vault_path)
            watch_vault(vault_path, vault_index, taxonomy=resolved.taxonomy, stop_event=stop_event)
        except Exception:
            logger.exception("auto-index/watch thread failed")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler = scheduler_factory(resolved) if scheduler_factory else None
        if scheduler is not None:
            scheduler.start()
        stop_event = threading.Event()
        # Gated on scheduler_factory, exactly like the scheduler above: test
        # apps never construct one, so tests never spawn this thread or touch
        # chromadb/the embedding model.
        if scheduler_factory is not None:
            threading.Thread(
                target=_auto_index_and_watch, args=(stop_event,), daemon=True
            ).start()
        # The inbound automations surface, on its own app and its own loopback
        # port. Gated on resolved.external_enabled, which defaults to False, so
        # an install that upgrades into this feature opens no port until asked
        # — and test apps, which never set it, bind nothing.
        external = await start_external_server(resolved)
        if external is not None:
            _app.state.external_port = external.port
        try:
            yield
        finally:
            if external is not None:
                await external.stop()
            stop_event.set()
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Argus", version=_version(), lifespan=lifespan)
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

    _shared_index: Callable[[], object] | None = None

    def _default_index_factory() -> object:
        """One VaultIndex for this app, not one per call.

        The construction and the reasoning both live in
        :func:`backend.rag.index.make_index_factory`; this wrapper only defers
        the import, so an install without the ``[rag]`` extras still boots.
        """
        from backend.rag.index import make_index_factory

        nonlocal _shared_index
        if _shared_index is None:
            _shared_index = make_index_factory(
                resolved.db_path.parent / "chroma", taxonomy=resolved.taxonomy
            )
        return _shared_index()

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
        build_ingest_router(
            resolved,
            generator or _default_generator("ingest"),
            index,
            job_runner=ingest_job_runner,
        )
    )
    app.include_router(build_system_router(resolved, model_prober, model_puller))
    app.include_router(build_tasks_router(resolved))
    app.include_router(build_flashcards_router(resolved))
    app.include_router(build_quick_links_router(resolved))
    app.include_router(build_search_router(resolved, index))
    app.include_router(build_index_router(resolved, index))
    app.include_router(build_review_router(resolved, planner or _default_planner()))
    app.include_router(build_briefing_router(resolved, briefing_composer or _default_composer()))
    app.include_router(build_insights_router(resolved))
    app.include_router(build_chat_router(resolved, chat_runner))
    app.include_router(build_automations_router(resolved))

    return app


def _production_scheduler(settings: Settings):
    from backend.features.briefing.service import make_agent_composer
    from backend.scheduler import build_scheduler

    return build_scheduler(settings, composer=make_agent_composer(settings))


app = create_app(scheduler_factory=_production_scheduler)
