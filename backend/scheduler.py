"""Background jobs: the 07:00 briefing and the nightly task-cache refresh.

Built by :func:`build_scheduler` but started only by the module-level app in
``backend.main`` — test apps never construct it, so tests never spawn threads.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.briefing.service import Composer, compose_briefing
from backend.vault.tasks import refresh_cache
from backend.vault.writer import write_briefing

logger = logging.getLogger("argus.scheduler")

BRIEFING_HOUR = 7
REFRESH_HOUR = 3
REINDEX_HOUR = 4


def run_briefing_job(settings: Settings, composer: Composer | None = None) -> str | None:
    """Compose and write today's briefing; never raises (logs instead)."""
    try:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            markdown = compose_briefing(settings, conn, composer=composer)
        finally:
            conn.close()
        path = write_briefing(settings.vault_path, markdown, taxonomy=settings.taxonomy)
        logger.info("briefing written to %s", path)
        return path
    except Exception:
        logger.exception("morning briefing failed")
        return None


def run_refresh_job(settings: Settings) -> None:
    """Nightly vault rescan into tasks_cache; never raises."""
    try:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            count = refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
        finally:
            conn.close()
        logger.info("task cache refreshed (%d open tasks)", count)
    except Exception:
        logger.exception("nightly task refresh failed")


def run_reindex_job(settings: Settings) -> None:
    """Nightly full vault reindex; never raises.

    The self-heal for watchdog events missed while the app was closed: the
    live watcher (:func:`backend.rag.watcher.watch_vault`) only sees changes
    made while Argus is running, so an edit made with the app shut is invisible
    to the index until something rebuilds it. This is that something.
    """
    try:
        from backend.rag.index import VaultIndex

        index = VaultIndex(settings.db_path.parent / "chroma", taxonomy=settings.taxonomy)
        result = index.reindex_all(settings.vault_path)
        if result.errors:
            logger.warning(
                "nightly reindex finished with %d error(s): %s", len(result.errors), result.errors
            )
        else:
            logger.info(
                "nightly reindex complete (%d chunks, %d files)", result.total_chunks, result.files
            )
    except Exception:
        logger.exception("nightly reindex failed")


def build_scheduler(settings: Settings, composer: Composer | None = None) -> BackgroundScheduler:
    """Create (but do not start) the background scheduler."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_briefing_job,
        CronTrigger(hour=BRIEFING_HOUR, minute=0),
        args=[settings, composer],
        id="morning-briefing",
    )
    scheduler.add_job(
        run_refresh_job,
        CronTrigger(hour=REFRESH_HOUR, minute=0),
        args=[settings],
        id="nightly-task-refresh",
    )
    scheduler.add_job(
        run_reindex_job,
        CronTrigger(hour=REINDEX_HOUR, minute=0),
        args=[settings],
        id="nightly-reindex",
    )
    return scheduler
