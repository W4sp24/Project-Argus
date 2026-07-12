"""Tests for the SQLite storage layer."""

import sqlite3
from pathlib import Path

import pytest

from backend.db import connect, init_schema


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
