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
    conn = connect(tmp_path / "friday.db")
    try:
        init_schema(conn)
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
