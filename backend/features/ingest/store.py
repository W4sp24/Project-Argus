"""Persistence for long-running jobs — what happened to every file, per stage.

Started as the ingest job store and still lives under ``features/ingest``,
but it is now the one durable record of every job that outlives its request:
an ingest, a vault reindex, a study guide, a practice exam. The `kind`
column says which; `params` carries the facts that have no column of their
own. Keeping one table (rather than one per feature) is what lets
:func:`running_job_id` answer "is anything already using the embedding
model?" across features -- two independent single-flight locks against the
same chroma directory could not.

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

import json
import sqlite3
import uuid
from typing import Any

#: Identifies *this* process. Rows carrying any other boot id belong to a job
#: whose thread died with a previous process, and are reconciled at startup.
#: The in-memory reindex `_State` this table replaced got that for free by
#: resetting on restart; a table does not, so reconciliation buys it back.
BOOT_ID = uuid.uuid4().hex

#: Stages that mean a file is finished, one way or another.
TERMINAL_STAGES = ("done", "failed", "skipped")

#: Job statuses that still occupy a slot.
ACTIVE_STATUSES = ("queued", "running")

#: Every kind of work this table records. Not a CHECK constraint -- see the
#: `kind` column's comment in :mod:`backend.core.db`: SQLite cannot alter one,
#: so a vocabulary expected to grow does not belong in the schema.
JOB_KINDS = ("ingest", "reindex", "guide", "exam", "relink", "deck")

#: Which kinds contend for one slot, and which run unconstrained.
#:
#: This is a deliberate behaviour change, not a refactor artefact. Before this
#: table was generalised, `POST /api/ingest/jobs` and `POST /api/index/reindex`
#: held *two independent* single-flight locks -- so an ingest and a reindex
#: could and did run at once, each loading its own copy of the bge-small
#: embedding model and both writing the same chroma directory, while
#: ``writer._git_snapshot`` runs git with ``check=False`` so the loser of the
#: resulting ``.git/index.lock`` race failed silently. Putting them in one
#: 'index' group makes them contend, which is what they should always have
#: done.
#:
#: Study generation shares nothing with an ingest -- a guide or an exam is an
#: LLM call plus a write into the course's ``study/`` folder, the one
#: sanctioned exception to I1, so it takes no git snapshot -- and it reads its
#: corpus in the request handler, before the job exists. Making a user wait
#: for a reindex to write an exam would be a restriction with nothing behind
#: it, so 'generate' is its own group rather than joining 'index'.
#:
#: It *is* a group, though, where it used to be nothing at all. Ungrouped,
#: every click spawned a thread: N clicks on GENERATE meant N daemon threads,
#: N SQLite connections, N event loops and N concurrent provider calls, all
#: pinning their own 60k-character prompt. The old comment reasoned only about
#: git and chroma and concluded "contends with nothing", which is true of the
#: vault and false of the machine.
#:
#: A relink *is* in the 'index' group, by the same test: it takes one
#: ``snapshot_vault`` and re-upserts every note it rewrites, so it contends for
#: the git index and the embedding model exactly as an ingest does.
SLOT_GROUPS: dict[str, str] = {
    "ingest": "index",
    "reindex": "index",
    "relink": "index",
    "guide": "generate",
    "exam": "generate",
    "deck": "generate",
}

#: How many jobs of a group may be active at once.
#:
#: 'index' is 1 and always was: one embedding model, one chroma directory, one
#: ``.git/index.lock``. 'generate' is 3 because generating a guide *and* a
#: deck at once is a thing the Course Hub deliberately offers -- its three
#: STUDIO buttons each track their own kind -- while a fourth concurrent
#: generation is a double-click rather than an intention.
#:
#: A kind absent from :data:`SLOT_GROUPS` is still unconstrained.
SLOT_CAPACITY: dict[str, int] = {"index": 1, "generate": 3}


def _slot_peers(kind: str) -> tuple[str, ...]:
    """Every kind that contends with ``kind``, including itself.

    ``()`` for a kind with no group, which callers read as "no slot to hold".
    """
    group = SLOT_GROUPS.get(kind)
    if group is None:
        return ()
    return tuple(peer for peer, name in SLOT_GROUPS.items() if name == group)


def _in_clause(values: tuple[str, ...]) -> str:
    """``('a', 'b')`` as SQL. Built rather than interpolated from the tuple repr.

    These are module constants, never user input, so this is not about
    injection -- it is that Python's repr of a one-element tuple is
    ``('a',)``, which is a syntax error in SQL. Dropping a stage from the list
    above would otherwise break these queries at runtime and nowhere else.
    """
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _decode_params(raw: str | None) -> dict[str, Any] | None:
    """``params`` as a dict, or ``None``. Never raises."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "kind": row["kind"],
        # Decoded here rather than at every call site: `params` is a wire
        # field, and a JSON string on one route and a dict on another is
        # exactly the drift `_job_row` exists to prevent. A row hand-edited to
        # something unparseable reports None rather than taking the poll
        # endpoint down with it.
        "params": _decode_params(row["params"]),
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
        "failed_stage": row["failed_stage"],
    }


#: How many jobs are kept. Every ingest, reindex, relink, guide, exam and deck
#: this install has ever run wrote a row here, plus one `ingest_job_items` row
#: per file, and nothing ever deleted any of them -- so the table grew for the
#: life of the vault in an app meant to run for months. Enforced in application
#: code rather than a sqlite trigger, the same way (and for the same stated
#: reason as) `automations.store.record_event`'s EVENT_RETENTION_CAP.
#:
#: Generous on purpose: `list_jobs` shows a short window, but a job's items are
#: the record of what an ingest did to each file, and that is worth keeping far
#: longer than anyone scrolls.
JOB_RETENTION_CAP = 500


def _prune(conn: sqlite3.Connection, *, protect: str | None = None) -> None:
    """Drop all but the newest ``JOB_RETENTION_CAP`` *finished* jobs.

    By ``created_at``, then ``rowid`` -- ``id`` is a uuid hex string, so
    ordering by it would delete essentially at random. Items go with their job
    through ``ON DELETE CASCADE``, which `connect()`'s ``PRAGMA foreign_keys=ON``
    makes real.

    Active jobs are exempt, and so is ``protect`` -- the row being created by
    the call that triggered this prune. Without either, a long-running job
    older than ``JOB_RETENTION_CAP`` newer ones was deleted underneath its own
    worker, which then found no item, logged "job %s vanished before it ran"
    and returned silently, after the model call had already been paid for.
    """
    keepers = list(ACTIVE_STATUSES)
    conn.execute(
        "DELETE FROM ingest_jobs WHERE id NOT IN ("
        "  SELECT id FROM ingest_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?"
        f") AND status NOT IN {_in_clause(tuple(keepers))} AND id IS NOT ?",
        (JOB_RETENTION_CAP, protect),
    )


class SlotBusyError(RuntimeError):
    """Raised by :func:`claim_job` when the kind's group is already full.

    Carries the blocking job so the route can name it in its 409 rather than
    saying only that something else is running.
    """

    def __init__(self, blocking: dict[str, Any]) -> None:
        self.blocking = blocking
        super().__init__(f"{blocking.get('kind', 'a job')} {blocking.get('id')} holds the slot")


def claim_job(conn: sqlite3.Connection, **kwargs: Any) -> str:
    """Take the slot and write the job row, atomically, or raise :class:`SlotBusyError`.

    Every route that starts a long job used to do this as two autocommitted
    statements: ask ``running_job`` whether anything was in the way, then call
    :func:`create_job`. Two simultaneous requests could both see an empty slot
    and both create -- so the 409 was advisory, and the thing it was advising
    against is the `.git/index.lock` race and the doubled embedding-model load
    that :data:`SLOT_GROUPS` exists to prevent. The desktop shell supervises
    three processes against one database, so this is not a theoretical window.

    ``BEGIN IMMEDIATE`` takes the write lock before the read, which makes the
    check and the claim one decision.
    """
    kind = str(kwargs.get("kind", "ingest"))
    conn.execute("BEGIN IMMEDIATE")
    try:
        blocking = _blocking_row(conn, kind, "*")
        if blocking is not None:
            raise SlotBusyError(_job_row(blocking))
        job_id = create_job(conn, _in_transaction=True, **kwargs)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return job_id


def create_job(
    conn: sqlite3.Connection,
    *,
    target: str,
    summary_prompt: str = "",
    filenames: list[str],
    note_style: str = "",
    kind: str = "ingest",
    params: dict[str, Any] | None = None,
    _in_transaction: bool = False,
) -> str:
    """Record a queued job and one queued item per file. Returns the job id.

    ``filenames`` may legitimately be empty: a full reindex does not know what
    it will touch until it has walked the vault, and records its items
    afterwards with :func:`add_items`.
    """
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO ingest_jobs "
        "(id, boot_id, status, kind, params, target, summary_prompt, note_style, total) "
        "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            BOOT_ID,
            kind,
            json.dumps(params) if params is not None else None,
            target,
            summary_prompt,
            note_style,
            len(filenames),
        ),
    )
    conn.executemany(
        "INSERT INTO ingest_job_items (job_id, filename, stage) VALUES (?, ?, 'queued')",
        [(job_id, name) for name in filenames],
    )
    _prune(conn, protect=job_id)
    # `claim_job` owns the transaction when it calls through here: committing
    # in the middle of it would release the slot lock before the row it is
    # protecting is safe.
    if not _in_transaction:
        conn.commit()
    return job_id


def merge_params(conn: sqlite3.Connection, job_id: str, values: dict[str, Any]) -> None:
    """Fold ``values`` into a job's ``params``, keeping what is already there.

    Merged rather than replaced because ``params`` carries both halves of a
    job's story: the request's inputs, written when the row is created, and
    the results that have no column of their own -- an exam's ``exam_id``, a
    generation's written ``path`` -- written when it finishes. Replacing would
    make the finished row unable to say what was asked for.
    """
    # In one transaction: the SELECT and the UPDATE were separate
    # autocommitted statements, so two writers folding different keys into one
    # job's params each read the pre-merge value and the second overwrote the
    # first. Safe today only because one thread owns a job, which nothing in
    # the type system says and nothing enforces.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT params FROM ingest_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            return
        merged = _decode_params(row["params"]) or {}
        merged.update(values)
        conn.execute(
            "UPDATE ingest_jobs SET params = ? WHERE id = ?", (json.dumps(merged), job_id)
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def add_items(conn: sqlite3.Connection, job_id: str, items: list[dict[str, Any]]) -> None:
    """Record files a job only learned about while running, already resolved.

    The full-vault reindex path: ``VaultIndex.reindex_all`` walks the vault
    itself and hands back per-file counts and errors in one go, so there is no
    moment at which a 'queued' row for each file would be true. Everything
    inserted here is therefore already at its terminal stage, and ``total``/
    ``done`` are recomputed from the rows rather than incremented -- the same
    reasoning as :func:`advance_item`, so calling this twice for one job
    cannot double-count.
    """
    if items:
        conn.executemany(
            "INSERT INTO ingest_job_items "
            "(job_id, filename, path, stage, chunks, error, failed_stage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    job_id,
                    item["filename"],
                    item.get("path"),
                    item["stage"],
                    item.get("chunks", 0),
                    item.get("error"),
                    item.get("failed_stage"),
                )
                for item in items
            ],
        )
    conn.execute(
        "UPDATE ingest_jobs SET "
        "  total = (SELECT COUNT(*) FROM ingest_job_items WHERE job_id = ingest_jobs.id), "
        "  done = (SELECT COUNT(*) FROM ingest_job_items "
        f"          WHERE job_id = ingest_jobs.id AND stage IN {_in_clause(TERMINAL_STAGES)}) "
        "WHERE id = ?",
        (job_id,),
    )
    conn.commit()


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
    failed_stage: str | None = None,
) -> None:
    """Move one file to its next stage, filling in whatever that stage learned.

    Every optional field is COALESCE'd rather than overwritten, so a later
    stage reporting only its own news does not erase the path an earlier one
    resolved.

    COALESCE means "do not clobber with NULL", not "write once": a later call
    passing a non-NULL value still replaces what is there. So ``error`` and
    ``failed_stage`` are passed together, in the single call that records the
    failure -- the stage an item stopped at and the reason it stopped are one
    fact, and writing them separately would let a later call pair a reason
    with the wrong stage, or leave either half standing alone.
    """
    conn.execute(
        "UPDATE ingest_job_items SET stage = ?, path = COALESCE(?, path), "
        "chunks = COALESCE(?, chunks), summary_path = COALESCE(?, summary_path), "
        "error = COALESCE(?, error), failed_stage = COALESCE(?, failed_stage) "
        "WHERE id = ?",
        (stage, path, chunks, summary_path, error, failed_stage, item_id),
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


def list_jobs(
    conn: sqlite3.Connection, limit: int = 20, *, kind: str | None = None
) -> list[dict[str, Any]]:
    """Recent jobs, newest first, without their items.

    ``kind=None`` lists every kind. A caller that wants one feature's history
    passes its kind -- the ingest history panel does, so folding reindexes and
    study generations into this table did not change what it shows.

    Deliberately no per-job item fan-out: the history list shows counts, and a
    caller that wants one job's detail asks for it by id.

    Ordered by ``rowid`` rather than by ``id``: ``created_at`` is
    second-granular, so two jobs started in the same second tie, and ``id`` is
    a random uuid -- a meaningless tiebreaker that made the order of a fast
    pair arbitrary. ``rowid`` is sqlite's implicit insertion counter, so it
    breaks the tie the way ``chat_threads`` uses its AUTOINCREMENT id.
    """
    where = "WHERE kind = ? " if kind else ""
    params: tuple[Any, ...] = (kind, limit) if kind else (limit,)
    return [
        _job_row(row)
        for row in conn.execute(
            f"SELECT * FROM ingest_jobs {where}ORDER BY created_at DESC, rowid DESC LIMIT ?",
            params,
        )
    ]


def latest_job(
    conn: sqlite3.Connection, kind: str, *, finished: bool = False
) -> dict[str, Any] | None:
    """The newest job of one kind. ``None`` if there is none.

    This is what lets a status endpoint be a *projection* over the table
    instead of a second, in-memory copy of the same three facts.

    ``finished=True`` skips jobs still in flight, which is a different
    question and both are asked: "is one running now?" reads the newest row of
    any status, while "when did the last one finish, and how?" must look past
    it or a rebuild in progress would erase the previous run's outcome from
    the readout the moment it started.
    """
    where = "kind = ? AND finished_at IS NOT NULL" if finished else "kind = ?"
    row = conn.execute(
        f"SELECT * FROM ingest_jobs WHERE {where} ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (kind,),
    ).fetchone()
    return _job_row(row) if row else None


def running_job_id(conn: sqlite3.Connection, kind: str = "ingest") -> str | None:
    """The job holding ``kind``'s slot, if any -- whatever kind that job is.

    One at a time, deliberately: a job in the 'index' group loads the
    embedding model, writes the chroma directory and takes a git snapshot, and
    two of any of those at once is the `.git/index.lock` race and a doubled
    model load. Queued counts as holding it -- the row is written before the
    thread starts.

    Kind-aware because the slot belongs to the *resource*, not to the route.
    An ingest asking this question must be told about a reindex already
    running, and vice versa; a study generation shares nothing with either and
    gets ``None`` unconditionally. See :data:`SLOT_GROUPS`.
    """
    row = _blocking_row(conn, kind, "id")
    return row["id"] if row else None


def slot_capacity(kind: str) -> int:
    """How many jobs of ``kind``'s group may run at once; 0 means unlimited."""
    group = SLOT_GROUPS.get(kind)
    return SLOT_CAPACITY.get(group, 1) if group else 0


def _blocking_row(conn: sqlite3.Connection, kind: str, columns: str) -> Any:
    """The job standing in ``kind``'s way, or ``None`` if there is room.

    "In the way" is a question about the *group's* capacity, not about any one
    job: with three generations allowed at once, the first two block nothing
    and the oldest of three is what a fourth is waiting behind. For a group of
    capacity one this is exactly the old behaviour -- the single active peer.
    """
    peers = _slot_peers(kind)
    if not peers:
        return None
    rows = conn.execute(
        f"SELECT {columns} FROM ingest_jobs WHERE status IN {_in_clause(ACTIVE_STATUSES)} "
        f"AND kind IN {_in_clause(peers)} ORDER BY rowid"
    ).fetchall()
    capacity = slot_capacity(kind)
    return rows[0] if len(rows) >= capacity and rows else None


def running_job(conn: sqlite3.Connection, kind: str = "ingest") -> dict[str, Any] | None:
    """Like :func:`running_job_id`, but the whole row.

    The reindex route needs the *kind* of the job in its way, not only its id:
    a second reindex trigger is an idempotent no-op that reports the rebuild
    already in flight, while an ingest in the way is a 409.
    """
    row = _blocking_row(conn, kind, "*")
    return _job_row(row) if row else None


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
