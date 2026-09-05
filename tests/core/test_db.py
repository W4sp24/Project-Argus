"""Tests for the SQLite storage layer."""

import sqlite3
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema


def test_connect_creates_parent_dirs_and_enables_wal(tmp_path: Path) -> None:
    db_path = tmp_path / ".argus" / "argus.db"

    conn = connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        assert db_path.parent.is_dir()
    finally:
        conn.close()


def test_init_schema_is_idempotent_and_creates_suggestions(tmp_path: Path) -> None:
    """The DDL itself must survive a second run, not merely be skipped.

    ``init_schema`` now returns early for a database this process has already
    initialised, so the cache is cleared between the two calls -- otherwise
    this would assert nothing about the script and would pass even if the
    migrations were not idempotent at all.
    """
    from backend.core.db import reset_schema_cache

    conn = connect(tmp_path / "friday.db")
    try:
        init_schema(conn)
        reset_schema_cache()
        init_schema(conn)  # must not raise

        conn.execute(
            "INSERT INTO suggestions (kind, payload_json, rationale) VALUES (?, ?, ?)",
            ("task", "{}", "test row"),
        )
        row = conn.execute("SELECT kind, status FROM suggestions").fetchone()
        assert row["kind"] == "task"
        assert row["status"] == "pending"
    finally:
        conn.close()


def test_suggestions_kind_is_constrained(tmp_path: Path) -> None:
    conn = connect(tmp_path / "friday.db")
    try:
        init_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO suggestions (kind, payload_json, rationale) VALUES (?, ?, ?)",
                ("bogus", "{}", "bad kind"),
            )
    finally:
        conn.close()


def test_an_older_database_gains_the_failed_stage_column(tmp_path) -> None:
    """There is no migration framework here: the column is added to SCHEMA for
    fresh databases and ALTERed in for existing ones. A database written before
    `failed_stage` existed has to survive the upgrade, because `CREATE TABLE IF
    NOT EXISTS` will not reshape a table that is already there."""
    from backend.core.db import connect, init_schema

    conn = connect(tmp_path / "argus.db")
    conn.execute(
        "CREATE TABLE ingest_job_items ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  job_id TEXT NOT NULL,"
        "  filename TEXT NOT NULL,"
        "  path TEXT,"
        "  stage TEXT NOT NULL,"
        "  chunks INTEGER NOT NULL DEFAULT 0,"
        "  summary_path TEXT,"
        "  error TEXT)"
    )
    conn.commit()

    init_schema(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ingest_job_items)")}
    assert "failed_stage" in columns
    conn.close()


def test_an_older_database_gains_the_kind_and_params_columns(tmp_path) -> None:
    """The job store serves every long-running job now, not only ingestion.

    Same shape of hazard as `failed_stage` above and the same fix: there is no
    migration framework, so `kind` and `params` are added to SCHEMA for fresh
    databases and ALTERed in for existing ones. `CREATE TABLE IF NOT EXISTS`
    is a no-op against a table that already exists, so without the guarded
    ALTERs an installed copy of Argus would keep its old four-column
    `ingest_jobs` and every read through `store._job_row` -- which hand-lists
    each field -- would fail with "no such column" on the first poll.

    The row written before the upgrade must also come out the other side
    saying what it was: `kind` defaults to 'ingest' precisely because every
    job recorded under the old schema was one.
    """
    conn = connect(tmp_path / "argus.db")
    conn.execute(
        "CREATE TABLE ingest_jobs ("
        "  id TEXT PRIMARY KEY,"
        "  boot_id TEXT NOT NULL,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  finished_at TEXT,"
        "  status TEXT NOT NULL,"
        "  target TEXT NOT NULL,"
        "  summary_prompt TEXT NOT NULL DEFAULT '',"
        "  note_style TEXT NOT NULL DEFAULT '',"
        "  total INTEGER NOT NULL DEFAULT 0,"
        "  done INTEGER NOT NULL DEFAULT 0,"
        "  error TEXT)"
    )
    conn.execute(
        "INSERT INTO ingest_jobs (id, boot_id, status, target) VALUES ('old', 'boot', 'ok', 'x')"
    )
    conn.commit()

    init_schema(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ingest_jobs)")}
    assert {"kind", "params"} <= columns

    from backend.features.ingest import store

    job = store.get_job(conn, "old")
    assert job is not None, "the pre-upgrade row must still be readable"
    assert job["kind"] == "ingest"
    assert job["params"] is None
    conn.close()


def test_an_older_database_gains_the_task_recurrence_column(tmp_path) -> None:
    """A recurring task must look recurring everywhere the user can see one.

    `tasks_cache` is a derived cache, rebuilt from markdown on every read, so
    this column needs no backfill -- but it does need the guarded ALTER. There
    is no migration framework here, and `CREATE TABLE IF NOT EXISTS` is a
    no-op against a table that already exists, so an installed copy of Argus
    would keep its old nine-column `tasks_cache` and every `refresh_cache`
    INSERT -- which hand-lists each column -- would die with "no such column"
    on the first agenda load. That is the whole task list, not one panel.
    """
    conn = connect(tmp_path / "argus.db")
    conn.execute(
        "CREATE TABLE tasks_cache ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  path TEXT NOT NULL,"
        "  line INTEGER NOT NULL,"
        "  text TEXT NOT NULL,"
        "  done INTEGER NOT NULL DEFAULT 0,"
        "  due TEXT,"
        "  scheduled TEXT,"
        "  priority TEXT,"
        "  tags TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("INSERT INTO tasks_cache (path, line, text) VALUES ('a.md', 1, 'old task')")
    conn.commit()

    init_schema(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks_cache)")}
    assert "recurrence" in columns
    row = conn.execute("SELECT text, recurrence FROM tasks_cache").fetchone()
    assert row["text"] == "old task"
    assert row["recurrence"] is None

    # The INSERT refresh_cache actually issues must work against the migrated
    # table -- the failure this test exists to catch is a write, not a read.
    conn.execute(
        "INSERT INTO tasks_cache"
        " (path, line, text, done, due, scheduled, priority, tags, recurrence)"
        " VALUES ('b.md', 2, 'water plants', 0, '2026-09-01', NULL, NULL, '', 'every week')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT recurrence FROM tasks_cache WHERE path = 'b.md'"
    ).fetchone()["recurrence"] == "every week"
    conn.close()


def test_the_schema_is_built_once_per_process_not_once_per_connection(tmp_path: Path) -> None:
    """There is no connection pool, so this ran on every connection in the app.

    The script is 42 statements plus ~15 `PRAGMA table_info` round-trips, the
    guarded ALTERs, four rebuild migrations and an unconditional UPDATE. Every
    HTTP request paid it; one chat turn paid it five or more times.
    """
    from backend.core.db import init_schema, reset_schema_cache

    db_path = tmp_path / "argus.db"

    first = connect(db_path)
    try:
        statements: list[str] = []
        first.set_trace_callback(statements.append)
        init_schema(first)
        first.set_trace_callback(None)
        assert any("CREATE TABLE" in sql for sql in statements), "the first call builds the schema"
    finally:
        first.close()

    # A *different* connection to the same file -- which is what every request
    # in the app opens -- must not repeat the work.
    second = connect(db_path)
    try:
        statements = []
        second.set_trace_callback(statements.append)
        init_schema(second)
        second.set_trace_callback(None)
        assert not any("CREATE TABLE" in sql for sql in statements), statements
        assert not any("ALTER TABLE" in sql for sql in statements), statements
    finally:
        second.close()

    # ...and the guard is per database, not global: a second database in the
    # same process still gets its schema.
    reset_schema_cache(str(db_path))
    other = connect(tmp_path / "other.db")
    try:
        statements = []
        other.set_trace_callback(statements.append)
        init_schema(other)
        other.set_trace_callback(None)
        assert any("CREATE TABLE" in sql for sql in statements)
    finally:
        other.close()


def test_an_in_memory_database_is_never_cached() -> None:
    """Two `:memory:` handles are two unrelated databases, and both report an
    empty file -- caching on that key would leave the second one schemaless."""
    import sqlite3

    from backend.core.db import init_schema

    for _ in range(2):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            init_schema(conn)
            assert conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] == 0
        finally:
            conn.close()
