"""Persistence for ingest jobs — what happened to every file, per stage.

Modelled on :mod:`backend.features.automations.store`: every function takes a
``sqlite3.Connection`` and never opens one. That matters more here than
anywhere else in the codebase. :func:`backend.core.db.connect` does not pass
``check_same_thread=False``, so a connection is bound to the thread that
created it -- and the ingest pipeline is the first background thread in Argus
that writes to the database. A connection captured from a request handler and
used on the job thread raises ``sqlite3.ProgrammingError``, so the job body
opens its own.

Writes are kept to short single statements with an immediate commit rather
than one transaction spanning a file's whole pipeline: embedding a file takes
seconds, and holding a write transaction open across it would block every
reader for that long. WAL plus ``connect``'s deliberate 30s busy timeout do
the rest.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

#: Identifies *this* process. Rows carrying any other boot id belong to a job
#: whose thread died with a previous process, and are reconciled at startup.
#: The in-memory reindex `_State` gets this for free by resetting on restart;
#: a table does not.
BOOT_ID = uuid.uuid4().hex

#: Stages that mean a file is finished, one way or another.
TERMINAL_STAGES = ("done", "failed", "skipped")

#: Job statuses that still occupy the single ingest slot.
ACTIVE_STATUSES = ("queued", "running")


def _in_clause(values: tuple[str, ...]) -> str:
    """``('a', 'b')`` as SQL. Built rather than interpolated from the tuple repr.

    These are module constants, never user input, so this is not about
    injection -- it is that Python's repr of a one-element tuple is
    ``('a',)``, which is a syntax error in SQL. Dropping a stage from the list
    above would otherwise break these queries at runtime and nowhere else.
    """
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "target": row["target"],
        "summary_prompt": row["summary_prompt"],
        "note_style": row["note_style"],
        "total": row["total"],
        "done": row["done"],
        "error": row["error"],
    }


def _item_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "path": row["path"],
        "stage": row["stage"],
        "chunks": row["chunks"],
        "summary_path": row["summary_path"],
        "error": row["error"],
    }


def create_job(
    conn: sqlite3.Connection,
    *,
    target: str,
    summary_prompt: str,
    filenames: list[str],
    note_style: str = "",
) -> str:
    """Record a queued job and one queued item per file. Returns the job id."""
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO ingest_jobs "
        "(id, boot_id, status, target, summary_prompt, note_style, total) "
        "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
        (job_id, BOOT_ID, target, summary_prompt, note_style, len(filenames)),
    )
    conn.executemany(
        "INSERT INTO ingest_job_items (job_id, filename, stage) VALUES (?, ?, 'queued')",
        [(job_id, name) for name in filenames],
    )
    conn.commit()
    return job_id


def start_job(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE ingest_jobs SET status = 'running' WHERE id = ?", (job_id,))
    conn.commit()


def advance_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    stage: str,
    path: str | None = None,
    chunks: int | None = None,
    summary_path: str | None = None,
    error: str | None = None,
) -> None:
    """Move one file to its next stage, filling in whatever that stage learned.

    Every optional field is COALESCE'd rather than overwritten, so a later
    stage reporting only its own news does not erase the path an earlier one
    resolved.
    """
    conn.execute(
        "UPDATE ingest_job_items SET stage = ?, path = COALESCE(?, path), "
        "chunks = COALESCE(?, chunks), summary_path = COALESCE(?, summary_path), "
        "error = COALESCE(?, error) WHERE id = ?",
        (stage, path, chunks, summary_path, error, item_id),
    )
    if stage in TERMINAL_STAGES:
        # Recomputed rather than incremented: an item can only be counted once
        # even if it is advanced to a terminal stage twice.
        conn.execute(
            "UPDATE ingest_jobs SET done = ("
            "  SELECT COUNT(*) FROM ingest_job_items"
            f"  WHERE job_id = ingest_jobs.id AND stage IN {_in_clause(TERMINAL_STAGES)}"
            ") WHERE id = (SELECT job_id FROM ingest_job_items WHERE id = ?)",
            (item_id,),
        )
    conn.commit()


def finish_job(
    conn: sqlite3.Connection, job_id: str, *, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE ingest_jobs SET status = ?, error = ?, finished_at = datetime('now') "
        "WHERE id = ?",
        (status, error, job_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    """One job with its items, or ``None``. This is what the poll endpoint returns."""
    row = conn.execute("SELECT * FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = _job_row(row)
    job["items"] = [
        _item_row(item)
        for item in conn.execute(
            "SELECT * FROM ingest_job_items WHERE job_id = ? ORDER BY id", (job_id,)
        )
    ]
    return job


def list_jobs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Recent jobs, newest first, without their items.

    Deliberately no per-job item fan-out: the history list shows counts, and a
    caller that wants one job's detail asks for it by id.

    Ordered by ``rowid`` rather than by ``id``: ``created_at`` is
    second-granular, so two jobs started in the same second tie, and ``id`` is
    a random uuid -- a meaningless tiebreaker that made the order of a fast
    pair arbitrary. ``rowid`` is sqlite's implicit insertion counter, so it
    breaks the tie the way ``chat_threads`` uses its AUTOINCREMENT id.
    """
    return [
        _job_row(row)
        for row in conn.execute(
            "SELECT * FROM ingest_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        )
    ]


def running_job_id(conn: sqlite3.Connection) -> str | None:
    """The job holding the ingest slot, if any.

    One at a time, deliberately: a job loads the embedding model and takes a
    git snapshot, and two of either at once is the `.git/index.lock` race and
    a doubled model load. Queued counts as holding it -- the row is written
    before the thread starts.
    """
    row = conn.execute(
        f"SELECT id FROM ingest_jobs WHERE status IN {_in_clause(ACTIVE_STATUSES)} "
        "ORDER BY rowid LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def reconcile_stale_jobs(conn: sqlite3.Connection) -> int:
    """Fail jobs left mid-flight by a process that is no longer running.

    Boot-scoped and called **once**, at router construction, not per
    connection: run against this process's own rows it would kill the job
    currently in flight. Returns how many jobs were reconciled.
    """
    conn.execute(
        "UPDATE ingest_job_items SET stage = 'failed', error = 'interrupted by restart' "
        f"WHERE stage NOT IN {_in_clause(TERMINAL_STAGES)} AND job_id IN ("
        f"  SELECT id FROM ingest_jobs WHERE status IN {_in_clause(ACTIVE_STATUSES)} "
        "  AND boot_id != ?"
        ")",
        (BOOT_ID,),
    )
    cursor = conn.execute(
        "UPDATE ingest_jobs SET status = 'failed', error = 'interrupted by restart', "
        "finished_at = datetime('now') "
        f"WHERE status IN {_in_clause(ACTIVE_STATUSES)} AND boot_id != ?",
        (BOOT_ID,),
    )
    conn.commit()
    return cursor.rowcount
