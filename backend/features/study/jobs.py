"""The bodies of the study-generation jobs: one guide, or one practice exam.

Study generation is the longest-running thing Argus does — the journal puts it
at "a few minutes" — and until this module existed it was a bare ``await``
held open inside the request. Navigating away or reloading the tab cancelled
nothing: the backend still generated the guide and still wrote it into the
vault, but the browser that asked for it never learned where it landed, and
there was no record anywhere that it had happened. That is the same class of
bug the ingest job store was built to fix, so these run through the same
store.

Two things are deliberately *not* here:

* **The corpus.** It is read in the request handler, before the job row
  exists, because "none of the selected sources are indexed" is a 422 about
  the request — answering it with a 202 and a job that fails a minute later
  would be strictly worse for the user.
* **A slot.** These jobs take no single-flight lock (see
  ``ingest.store.SLOT_GROUPS``): a generation is an LLM call plus a write into
  the course's ``study/`` folder, which is the one sanctioned exception to I1
  and therefore takes no git snapshot. It contends with nothing.

Like every job body in this codebase, neither function raises: they run on a
daemon thread, so an escaping exception would surface only in a log nobody is
watching. Failures are recorded against the row they belong to.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.study.practice_exam import (
    Generator,
    StudyError,
    generate_practice_exam,
)
from backend.features.study.study_guide import generate_study_guide

logger = logging.getLogger("argus.study")

#: The stage a generation sits at while the model is working. Reused from the
#: ingest vocabulary rather than adding a 'generating' value: `stage` lives in
#: a CHECK constraint, SQLite cannot alter one, and a new value would cost a
#: create/copy/drop/rename table rebuild to buy a label the frontend can
#: perfectly well say for itself.
GENERATING = "summarizing"


def _run_async(coro: Any) -> Any:
    """Await one coroutine from this synchronous worker thread.

    ``asyncio.run`` is safe here for exactly the reason it is safe in
    ``ingest.pipeline._generate``: this is a worker thread with no running
    loop of its own.
    """
    return asyncio.run(coro)


def _job_item(conn: Any, job_id: str) -> dict[str, Any] | None:
    """The single item every study job carries, or ``None`` if the job vanished.

    One item, not none: a generation produces one file, and giving it a row
    means the written path has a home (``summary_path``) that the poll
    endpoint already projects — no new column, and the same shape the ingest
    readout renders.
    """
    job = store.get_job(conn, job_id)
    if job is None or not job["items"]:
        return None
    return job["items"][0]


def run_guide_job(
    job_id: str,
    *,
    settings: Settings,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    scope: str,
) -> None:
    """Generate one study guide and record where it landed. Never raises."""
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        item = _job_item(conn, job_id)
        if item is None:
            logger.warning("study guide job %s vanished before it ran", job_id)
            return
        store.start_job(conn, job_id)
        store.advance_item(conn, item["id"], stage=GENERATING)
        path = _run_async(
            generate_study_guide(
                settings.vault_path,
                generator,
                corpus,
                course,
                scope,
                taxonomy=settings.taxonomy,
            )
        )
        store.advance_item(conn, item["id"], stage="done", path=path, summary_path=path)
        # Mirrored into `params` as well as onto the item: a caller polling the
        # job for a *result* reads one place regardless of kind, and an exam's
        # `exam_id` has nowhere else to live at all.
        store.merge_params(conn, job_id, {"path": path})
        store.finish_job(conn, job_id, status="ok")
    except Exception as exc:
        _fail(conn, job_id, exc)
    finally:
        conn.close()


def run_exam_job(
    job_id: str,
    *,
    settings: Settings,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    topics: str | None,
    n: int,
    difficulty: str,
) -> None:
    """Generate one practice exam and record its id and path. Never raises."""
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        item = _job_item(conn, job_id)
        if item is None:
            logger.warning("practice exam job %s vanished before it ran", job_id)
            return
        store.start_job(conn, job_id)
        store.advance_item(conn, item["id"], stage=GENERATING)
        exam_id, built, path = _run_async(
            generate_practice_exam(
                settings.vault_path,
                conn,
                generator,
                corpus,
                course,
                topics,
                n,
                difficulty,
                taxonomy=settings.taxonomy,
            )
        )
        store.advance_item(conn, item["id"], stage="done", path=path, summary_path=path)
        store.merge_params(
            conn,
            job_id,
            # `exam_id` is the one result with no column anywhere in the job
            # tables, and it is the only handle the quiz UI has — without it a
            # finished exam job could say where the markdown went but not how
            # to sit the exam.
            {"exam_id": exam_id, "path": path, "questions": len(built.questions)},
        )
        store.finish_job(conn, job_id, status="ok")
    except Exception as exc:
        _fail(conn, job_id, exc)
    finally:
        conn.close()


def _fail(conn: Any, job_id: str, exc: BaseException) -> None:
    """Record a generation failure against its own row.

    ``StudyError`` is the expected half — an empty corpus, a generator that
    returned nothing usable, every question failing its citation check. It is
    a 422 on the synchronous path, and there is no status code to return on
    this one, so the message has to reach the user through the job or not at
    all.
    """
    if not isinstance(exc, StudyError):
        logger.exception("study job %s failed", job_id)
    else:
        logger.warning("study job %s failed: %s", job_id, exc)
    try:
        job = store.get_job(conn, job_id)
        if job and job["items"]:
            store.advance_item(
                conn,
                job["items"][0]["id"],
                stage="failed",
                failed_stage=GENERATING,
                error=str(exc),
            )
        store.finish_job(conn, job_id, status="failed", error=str(exc))
    except Exception:
        logger.exception("study job %s could not even record its own failure", job_id)
