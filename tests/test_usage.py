"""Tests for token-usage logging and GET /api/usage (redesign §14)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import usage
from backend.config import Settings
from backend.db import connect, init_schema
from backend.main import create_app
from backend.usage import record_result_usage, record_usage, usage_report


def test_record_usage_inserts_row(tmp_path: Path) -> None:
    db_path = tmp_path / "argus.db"
    record_usage(db_path, "chat", 100, 50, model="claude-sonnet-4")

    conn = connect(db_path)
    row = conn.execute("SELECT * FROM token_usage").fetchone()
    conn.close()
    assert row["feature"] == "chat"
    assert row["session_id"] == usage.SESSION_ID
    assert row["input_tokens"] == 100 and row["output_tokens"] == 50
    assert row["model"] == "claude-sonnet-4"


def test_record_usage_swallows_errors(tmp_path: Path) -> None:
    record_usage(tmp_path, "chat", 1, 1)  # db path is a directory -> sqlite error
    # No exception = pass: a logging failure must never break the caller.


def test_record_result_usage_reads_sdk_message(tmp_path: Path) -> None:
    class FakeResult:
        usage = {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 99}

    db_path = tmp_path / "argus.db"
    record_result_usage(db_path, "planner", FakeResult(), model="claude-opus-4-8")

    conn = connect(db_path)
    row = conn.execute("SELECT * FROM token_usage").fetchone()
    conn.close()
    assert (row["input_tokens"], row["output_tokens"]) == (7, 3)
    assert row["cache_read_input_tokens"] == 99
    assert row["feature"] == "planner"


def test_record_result_usage_tolerates_missing_usage(tmp_path: Path) -> None:
    record_result_usage(tmp_path / "argus.db", "chat", object())  # no .usage -> no row, no raise


def _seed(
    conn,
    ts: str,
    feature: str,
    session_id: str,
    tokens_in: int,
    tokens_out: int,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO token_usage (ts, feature, session_id, model, input_tokens, output_tokens,"
        " cache_creation_input_tokens, cache_read_input_tokens)"
        " VALUES (?, ?, ?, 'claude-sonnet-5', ?, ?, ?, ?)",
        (ts, feature, session_id, tokens_in, tokens_out, cache_creation, cache_read),
    )
    conn.commit()


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_session_report_is_per_exchange_and_scoped(conn) -> None:
    _seed(conn, "2026-07-15 09:00:00", "chat", usage.SESSION_ID, 100, 50, cache_read=20)
    _seed(conn, "2026-07-15 09:05:00", "briefing", usage.SESSION_ID, 200, 80)
    _seed(conn, "2026-07-01 09:00:00", "chat", "older-session", 999, 999)

    report = usage_report(conn, "session")
    assert report.session_id == usage.SESSION_ID
    assert (report.input_tokens, report.output_tokens) == (300, 130)
    assert report.cache_read_input_tokens == 20
    assert report.total_tokens == 450
    assert len(report.series) == 2, "session series is one point per exchange"
    assert report.estimated_cost_usd > 0
    features = {f.feature: f.total_tokens for f in report.features}
    assert features == {"chat": 170, "briefing": 280}


def test_week_report_groups_by_day(conn) -> None:
    conn.execute(
        "INSERT INTO token_usage (ts, feature, session_id, model, input_tokens, output_tokens)"
        " VALUES (datetime('now', '-1 days'), 'chat', 's1', '', 10, 5),"
        " (datetime('now', '-1 days'), 'chat', 's1', '', 20, 5),"
        " (datetime('now'), 'planner', 's1', '', 30, 5),"
        " (datetime('now', '-30 days'), 'chat', 's1', '', 999, 999)"
    )
    conn.commit()

    report = usage_report(conn, "week")
    assert report.total_tokens == 75, "rows older than 7 days are excluded"
    assert len(report.series) == 2, "week series is one point per day"
    assert report.series[0].total_tokens == 40


def test_all_report_groups_by_week(conn) -> None:
    _seed(conn, "2026-06-01 09:00:00", "chat", "s1", 10, 0)
    _seed(conn, "2026-06-02 09:00:00", "chat", "s1", 20, 0)
    _seed(conn, "2026-07-14 09:00:00", "chat", "s1", 40, 0)

    report = usage_report(conn, "all")
    assert report.total_tokens == 70
    assert len(report.series) == 2, "all-time series is one point per week"
    assert report.series[0].total_tokens == 30


def test_usage_endpoint(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(_vault_path=vault)
    record_usage(settings.db_path, "chat", 1000, 400, model="claude-haiku")

    client = TestClient(create_app(settings))
    payload = client.get("/api/usage", params={"range": "session"}).json()
    assert payload["total_tokens"] == 1400
    assert payload["range"] == "session"
    assert payload["features"][0]["feature"] == "chat"
    assert payload["estimated_cost_usd"] > 0
    assert len(payload["series"]) == 1

    assert client.get("/api/usage", params={"range": "nope"}).status_code == 422
