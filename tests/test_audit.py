"""Tests for the prompt audit log: path lists only, never content (I3)."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.audit import log_prompt, recent
from backend.config import Settings
from backend.db import connect, init_schema
from backend.main import create_app


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "friday.db"


@pytest.fixture()
def conn(db_path: Path) -> sqlite3.Connection:
    connection = connect(db_path)
    init_schema(connection)
    yield connection
    connection.close()


def test_log_and_recent_roundtrip(db_path: Path, conn: sqlite3.Connection) -> None:
    log_prompt(db_path, "chat", "claude-opus-4-8", ["10-Daily/2026-07-13.md", "Welcome.md"])

    entries = recent(conn)
    assert len(entries) == 1
    assert entries[0].entry_point == "chat"
    assert entries[0].model == "claude-opus-4-8"
    assert entries[0].paths == ["10-Daily/2026-07-13.md", "Welcome.md"]


def test_audit_stores_paths_only_no_content(db_path: Path, conn: sqlite3.Connection) -> None:
    secret = "the user's private prompt text"
    log_prompt(db_path, "chat", "m", ["note.md"])

    row = conn.execute("SELECT * FROM audit").fetchone()
    stored = json.dumps({key: row[key] for key in row.keys()})
    assert secret not in stored
    assert json.loads(row["paths_json"]) == ["note.md"]


def test_log_prompt_never_raises(tmp_path: Path) -> None:
    log_prompt(tmp_path / "no" / "such\0dir" / "db", "chat", "m", ["x.md"])  # swallowed


def test_planner_context_logs_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "30-Areas").mkdir(parents=True)
    (vault / "30-Areas" / "assistant-preferences.md").write_text("prefs", encoding="utf-8")
    queue = vault / "15-Courses" / "ES101" / "study"
    queue.mkdir(parents=True)
    (queue / "review-queue.md").write_text("- [ ] topic\n", encoding="utf-8")

    from backend.agent.planner import _planner_context

    settings = Settings(_vault_path=vault)
    conn = connect(settings.db_path)
    init_schema(conn)
    _planner_context(settings, conn, "plan my day")

    entries = recent(conn)
    assert entries and entries[0].entry_point == "planner"
    assert "30-Areas/assistant-preferences.md" in entries[0].paths
    assert "15-Courses/ES101/study/review-queue.md" in entries[0].paths
    conn.close()


def test_audit_endpoint(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(_vault_path=vault)
    log_prompt(settings.db_path, "briefing", "claude-opus-4-8", ["10-Daily/x.md"])

    client = TestClient(create_app(settings, briefing_composer=lambda data: "x"))
    payload = client.get("/api/audit").json()
    assert payload[0]["entry_point"] == "briefing"
    assert payload[0]["paths"] == ["10-Daily/x.md"]
