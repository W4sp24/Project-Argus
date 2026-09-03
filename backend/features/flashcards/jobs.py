"""The body of a deck-generation job.

Mirrors ``backend/features/study/jobs.py`` exactly, and for the same reason:
generating a deck from a corpus is a model call that runs for as long as a
study guide does, so it cannot be a request held open. It runs through the
same job store, is polled by the same endpoint, and appears in the same tray.

Like every job body in this codebase this function never raises. It runs on a
daemon thread, so an escaping exception would surface only in a log nobody is
watching; failures are recorded against the row they belong to.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.flashcards import generate, store
from backend.features.flashcards.store import FlashcardsError
from backend.features.ingest import store as jobstore
from backend.features.study.practice_exam import Generator

logger = logging.getLogger("argus.flashcards")

#: The stage a generation sits at while the model is working. Reused from the
#: ingest vocabulary rather than adding a value: `stage` lives in a CHECK
#: constraint, SQLite cannot alter one, and a new value would cost a
#: create/copy/drop/rename rebuild to buy a label the frontend can say for
#: itself.
GENERATING = "summarizing"


def _job_item(conn: Any, job_id: str) -> dict[str, Any] | None:
    job = jobstore.get_job(conn, job_id)
    if job is None or not job["items"]:
        return None
    return job["items"][0]


def run_deck_job(
    job_id: str,
    *,
    settings: Settings,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    deck_id: int,
    n: int,
) -> None:
    """Generate cards into an existing deck. Never raises.

    The deck row is created by the request handler, before this runs, so a
    202 can name the deck the cards will land in. A generation that fails
    therefore leaves an empty deck rather than nothing -- which is the honest
    outcome: the user asked for a deck, and it exists, and it is empty for a
    reason the job row records.
    """
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        item = _job_item(conn, job_id)
        if item is None:
            logger.warning("deck job %s vanished before it ran", job_id)
            return
        jobstore.start_job(conn, job_id)
        jobstore.advance_item(conn, item["id"], stage=GENERATING)

        cards = asyncio.run(generate.generate_cards(generator, corpus, course, n))
        added = store.add_cards(conn, deck_id, cards)

        jobstore.advance_item(conn, item["id"], stage="done")
        jobstore.merge_params(conn, job_id, {"deck_id": deck_id, "cards": added})
        jobstore.finish_job(conn, job_id, status="ok")
    except Exception as exc:  # noqa: BLE001 - recorded, never raised
        _fail(conn, job_id, exc)
    finally:
        conn.close()


def _fail(conn: Any, job_id: str, exc: BaseException) -> None:
    """Record a generation failure against its own row.

    ``FlashcardsError`` is the expected half -- an empty corpus, a reply with
    nothing card-shaped in it. There is no status code left to return once the
    202 has gone out, so the message reaches the user through the job or not at
    all.
    """
    if isinstance(exc, FlashcardsError):
        logger.warning("deck job %s failed: %s", job_id, exc)
    else:
        logger.exception("deck job %s failed", job_id)
    try:
        job = jobstore.get_job(conn, job_id)
        if job and job["items"]:
            jobstore.advance_item(
                conn,
                job["items"][0]["id"],
                stage="failed",
                failed_stage=GENERATING,
                error=str(exc),
            )
        jobstore.finish_job(conn, job_id, status="failed", error=str(exc))
    except Exception:
        logger.exception("deck job %s could not even record its own failure", job_id)
