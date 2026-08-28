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
