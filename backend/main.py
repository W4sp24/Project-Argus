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
from backend.features.calendar.router import build_calendar_router
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
from backend.rag.select import select_corpus, topic_queries

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
    ocr: object | None = None,
) -> FastAPI:
    """Build the FastAPI app around the given (or default) settings.

    ``chat_runner``, ``generator``, ``index_factory``, ``planner``,
    ``briefing_composer``, ``model_prober`` and ``model_puller`` are injectable
    so tests run with fakes instead of the agent SDK / embedding model / a live
    provider. ``scheduler_factory`` is only passed by the module-level app
    below — test apps never start background threads.

    ``ingest_job_runner`` keeps its name but is no longer ingest-only: it is
    how *every* long-running job is scheduled (ingest, reindex, study
    generation), because they all share one durable store and a test that
    wants one of them deterministic wants all of them deterministic.
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
    _shared_index_lock = threading.Lock()

    def _default_index_factory() -> object:
        """One VaultIndex for this app, not one per call.

        The construction and the reasoning both live in
        :func:`backend.rag.index.make_index_factory`; this wrapper only defers
        the import, so an install without the ``[rag]`` extras still boots.

        Double-checked under a lock for the same reason ``make_index_factory``
        is: the boot indexer thread calls this at startup while the first HTTP
        requests are already arriving, and an unsynchronised check-then-set
        hands those two callers a *factory each*. Two factories are two
        ``VaultIndex`` instances over one chroma directory, which is not merely
        wasteful -- the second ``PersistentClient`` releases the system the
        first is using -- so the memoisation that makes this correct has to
        hold across threads, not just across calls.
        """
        from backend.rag.index import make_index_factory

        nonlocal _shared_index
        if _shared_index is None:
            with _shared_index_lock:
                if _shared_index is None:
                    _shared_index = make_index_factory(
                        resolved.db_path.parent / "chroma",
                        taxonomy=resolved.taxonomy,
                        ocr=ocr if ocr is not None else _default_ocr(),
                    )
        return _shared_index()

    def _default_ocr():
        """How hard to try on a PDF page that has no text layer.

        This is the composition root for the OCR pass, and it has to be: it is
        the one place allowed to hold ``rag`` and ``agent`` at the same time.
        ``rag/`` must not import ``agent/`` -- the dependency runs the other
        way -- so the vision escalation and the page cache both reach the
        extractor as plain callables built here.

        ``describe`` is synchronous because ``extract_blocks`` is reached from
        ``VaultIndex.upsert_file`` on a worker thread inside ``reindex_all``'s
        loop; ``asyncio.run`` there is the same idiom the study and ingest job
        bodies already use, and it is safe for the same reason -- a worker
        thread with no running loop of its own.
        """
        from backend.core.db import connect, init_schema
        from backend.core.extraction_cache import get_page, put_page
        from backend.rag.extractors.ocr import OcrPolicy

        # A connection per call rather than one held open: these run on the
        # indexer's worker thread, and sqlite3 connections are not shareable
        # across threads. `with connect(...)` would be a transaction scope,
        # not a close, which is why the repo spells this try/finally.
        def _with_conn(work):
            conn = connect(resolved.db_path)
            try:
                init_schema(conn)
                return work(conn)
            finally:
                conn.close()

        def cache_get(content_hash: str, page: int):
            return _with_conn(lambda conn: get_page(conn, content_hash, page))

        def cache_put(content_hash: str, page: int, text: str, meta: dict) -> None:
            _with_conn(lambda conn: put_page(conn, content_hash, page, text, meta))

        def describe(png: bytes, prompt: str) -> str:
            import asyncio

            from backend.agent.generate import agent_describe_image

            return asyncio.run(
                agent_describe_image(
                    png,
                    prompt,
                    feature="ocr",
                    db_path=resolved.db_path,
                    settings=resolved,
                )
            )

        return OcrPolicy(
            describe=describe,
            # 0 unless the user asked for it: a fresh install must never
            # quietly spend tokens transcribing a 400-page scan.
            max_vision_pages=resolved.ocr_vision_pages,
            cache_get=cache_get,
            cache_put=cache_put,
        )

    def _default_job_pool() -> Callable[[Callable[[], None]], None]:
        """One bounded pool for every long-running job in the process.

        Each router carried its own ``threading.Thread(daemon=True).start()``,
        which is unbounded by construction: a job is a whole pipeline --
        its own SQLite connection, its own asyncio event loop, an embedding
        model or a provider call, and a prompt of up to 60,000 characters held
        for the duration. Nothing capped how many ran at once, so a user who
        clicked through a course could have a dozen alive together.

        The slot groups in ``ingest.store`` bound how many jobs of one *kind*
        are accepted; this bounds how many threads exist at all, including the
        kinds that hold no slot.

        Sized at the sum of the slot capacities plus headroom for the
        unslotted kinds, and daemon threads so the pool never delays shutdown
        -- the same posture the threads it replaces had.
        """
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="argus-job")

        def submit(run: Callable[[], None]) -> None:
            def guarded() -> None:
                try:
                    run()
                except Exception:
                    # A job body already records its own failure on the job
                    # row; this is the last resort for one that died before
                    # it could. Without it the exception is swallowed into a
                    # Future nobody reads.
                    logger.exception("job failed outside its own error handling")

            pool.submit(guarded)

        return submit

    # Built once per app, not per router: the whole point is that every
    # long-running job in the process shares one bounded pool. A test that
    # injects its own synchronous runner never constructs it.
    job_runner = ingest_job_runner or _default_job_pool()

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
    # Every router below receives this; exposing it too lets the boot thread's
    # "one index per process" invariant be asserted directly rather than
    # inferred from a router's behaviour.
    app.state.index_factory = index

    app.include_router(build_notes_router(resolved, index))
    app.include_router(build_journal_router(resolved))
    app.include_router(
        build_study_router(
            resolved,
            generator or _default_generator("study"),
            index,
            job_runner=job_runner,
        )
    )
    app.include_router(
        build_ingest_router(
            resolved,
            generator or _default_generator("ingest"),
            index,
            job_runner=job_runner,
        )
    )
    app.include_router(build_system_router(resolved, model_prober, model_puller, index))
    app.include_router(build_tasks_router(resolved))
    app.include_router(
        build_flashcards_router(
            resolved,
            generator or _default_generator("flashcards"),
            # The same corpus the study guide and the practice exam read, so a
            # generated deck honours the SOURCES selection instead of parsing
            # one hand-authored file that nothing writes.
            # `index` is the factory, not an instance -- the study router calls it
            # the same way. Calling it per request is what keeps a test's fake
            # index injectable.
            lambda course, sources, topics=None: select_corpus(
                index(),
                course=course,
                paths=sources,
                queries=topic_queries(topics),
                vault_path=resolved.vault_path,
                taxonomy=resolved.taxonomy,
            ).chunks,
            job_runner=job_runner,
        )
    )
    app.include_router(build_quick_links_router(resolved))
    app.include_router(build_search_router(resolved, index))
    app.include_router(build_index_router(resolved, index, job_runner=job_runner))
    app.include_router(build_review_router(resolved, planner or _default_planner()))
    app.include_router(build_briefing_router(resolved, briefing_composer or _default_composer()))
    app.include_router(build_insights_router(resolved))
    # The shared index, not one of chat's own: ChatAgent used to build a second
    # VaultIndex (and so a second embedding model) alongside the one every
    # other router already shares.
    # The summarizer is the plain generator under a different feature label,
    # so compaction shows up in token usage as its own line rather than
    # inflating chat's. A test app passing `chat_runner` gets no summarizer at
    # all, which leaves history budgeted the way it always was.
    app.include_router(
        build_chat_router(
            resolved,
            chat_runner,
            index,
            summarizer=None if chat_runner else _default_generator("chat-summary"),
        )
    )
    app.include_router(build_automations_router(resolved))
    app.include_router(build_calendar_router(resolved))

    return app


def _production_scheduler(settings: Settings):
    from backend.features.briefing.service import make_agent_composer
    from backend.scheduler import build_scheduler

    return build_scheduler(settings, composer=make_agent_composer(settings))


app = create_app(scheduler_factory=_production_scheduler)
