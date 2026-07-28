"""Tests for the multi-agent usage registry, its migration, and /api/usage/agents."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.main import create_app
from backend.telemetry import claude_cli, scan
from backend.telemetry.agents import registry
from backend.telemetry.agents.codex import CodexSource

OLD_SCHEMA = """
CREATE TABLE cli_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT NOT NULL,
    ts            TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE cli_usage_files (
    path       TEXT PRIMARY KEY,
    mtime_ns   INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_agent_column_migration_backfills_claude_code(tmp_path: Path) -> None:
    """Everything recorded before the column existed came from Claude Code."""
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(OLD_SCHEMA)
    raw.execute(
        "INSERT INTO cli_usage (file_path, ts, model, input_tokens, output_tokens)"
        " VALUES ('old.jsonl', '2026-07-01 09:00:00', 'claude-sonnet-5', 10, 5)"
    )
    raw.execute("INSERT INTO cli_usage_files (path, mtime_ns, size) VALUES ('old.jsonl', 1, 2)")
    raw.commit()
    raw.close()

    connection = connect(db_path)
    init_schema(connection)
    try:
        assert connection.execute("SELECT agent FROM cli_usage").fetchone()["agent"] == "claude-code"
        assert (
            connection.execute("SELECT agent FROM cli_usage_files").fetchone()["agent"]
            == "claude-code"
        )
        # And the pre-existing row is still reachable through the filtered read.
        rows = scan.fetch_rows(connection, "all", claude_cli.AGENT_ID)
        assert len(rows) == 1
    finally:
        connection.close()


def test_sync_rows_scopes_the_high_water_mark_per_agent(tmp_path: Path, conn) -> None:
    """Two agents scanning must not evict each other's rows."""
    a = tmp_path / "a.jsonl"
    a.write_text("", encoding="utf-8")

    def parse_one(_: Path):
        yield {
            "ts": "2026-07-20T09:00:00.000Z",
            "model": "m",
            "input_tokens": 5,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

    assert scan.sync_rows(conn, "claude-code", [a], parse_one) == 1
    assert scan.sync_rows(conn, "claude-code", [a], parse_one) == 0, "unchanged file is skipped"

    b = tmp_path / "b.jsonl"
    b.write_text("", encoding="utf-8")
    assert scan.sync_rows(conn, "codex", [b], parse_one) == 1

    counts = {
        row["agent"]: row["n"]
        for row in conn.execute("SELECT agent, COUNT(*) AS n FROM cli_usage GROUP BY agent")
    }
    assert counts == {"claude-code": 1, "codex": 1}


def _seed(conn, agent: str, model: str, tokens_in: int, tokens_out: int) -> None:
    conn.execute(
        "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens, output_tokens)"
        " VALUES ('f', ?, datetime('now'), ?, ?, ?)",
        (agent, model, tokens_in, tokens_out),
    )
    conn.commit()


def test_agents_report_lists_every_source_even_when_absent(tmp_path: Path, conn, monkeypatch) -> None:
    """An undetected agent reports zeroes and a hint — it never disappears."""
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    report = registry.agents_report(conn, "today")

    ids = [agent.id for agent in report.agents]
    assert ids == ["claude-code", "claude-subagents", "codex"]
    assert all(not agent.detected for agent in report.agents)
    assert all(agent.install_hint for agent in report.agents)
    assert all(agent.builtin for agent in report.agents)
    assert report.combined.total_tokens == 0


def test_agents_report_splits_and_combines(tmp_path: Path, conn, monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    _seed(conn, "claude-code", "claude-sonnet-5", 1_000_000, 100_000)
    _seed(conn, "codex", "gpt-5-codex", 200_000, 20_000)

    report = registry.agents_report(conn, "today")
    by_id = {agent.id: agent for agent in report.agents}

    assert by_id["claude-code"].total_tokens == 1_100_000
    assert by_id["codex"].total_tokens == 220_000
    assert report.combined.total_tokens == 1_320_000

    # Each agent is priced by its own table: Claude at 3/15, Codex at 1.25/10.
    assert by_id["claude-code"].estimated_cost_usd == pytest.approx(4.5, rel=1e-3)
    assert by_id["codex"].estimated_cost_usd == pytest.approx(0.45, rel=1e-3)
    assert report.combined.estimated_cost_usd == pytest.approx(4.95, rel=1e-3)


def test_combined_view_merges_a_model_seen_under_two_agents(
    tmp_path: Path, conn, monkeypatch
) -> None:
    """One row per model, not one per (agent, model) — and unique React keys."""
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    _seed(conn, "claude-code", "claude-sonnet-5", 1_000_000, 100_000)
    _seed(conn, "claude-subagents", "claude-sonnet-5", 500_000, 50_000)

    combined = registry.agents_report(conn, "today", None).combined
    assert [m.model for m in combined.models] == ["claude-sonnet-5"]
    assert combined.models[0].total_tokens == 1_650_000
    # Both slices are Anthropic-priced, so the merged cost is the sum of both.
    assert combined.models[0].estimated_cost_usd == pytest.approx(6.75, rel=1e-3)
    assert combined.models[0].unpriced is False


def test_a_partially_priced_model_is_still_named_as_incomplete(
    tmp_path: Path, conn, monkeypatch
) -> None:
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    _seed(conn, "claude-code", "claude-sonnet-5", 1_000_000, 100_000)
    _seed(conn, "codex", "claude-sonnet-5", 1_000_000, 100_000)  # no Codex rate for it

    combined = registry.agents_report(conn, "today", None).combined
    assert combined.models[0].estimated_cost_usd == pytest.approx(4.5, rel=1e-3)
    assert "claude-sonnet-5" in combined.unpriced_models, "a partial figure must say so"


def test_unknown_codex_model_is_named_not_guessed(tmp_path: Path, conn, monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    _seed(conn, "codex", "gpt-9-unreleased", 1_000_000, 100_000)

    codex = next(a for a in registry.agents_report(conn, "today").agents if a.id == "codex")
    assert codex.estimated_cost_usd == 0.0
    assert codex.unpriced_models == ["gpt-9-unreleased"]
    assert codex.models[0].unpriced is True


def test_today_buckets_by_hour_not_day(conn) -> None:
    """A single day bucketed by day is one point, which draws as a flat block.

    Rows go in as UTC (that is what the column holds), written as the UTC
    instant of 09:00 and 14:00 *local* — so the assertion is on the reader's
    clock and holds in any timezone.
    """
    conn.execute(
        "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens) VALUES"
        " ('f', 'claude-code', datetime('now', 'localtime', 'start of day', '+9 hours', 'utc'),"
        "  'm', 10),"
        " ('f', 'claude-code', datetime('now', 'localtime', 'start of day', '+9 hours', 'utc'),"
        "  'm', 5),"
        " ('f', 'claude-code', datetime('now', 'localtime', 'start of day', '+14 hours', 'utc'),"
        "  'm', 20)"
    )
    conn.commit()

    points = scan.series(scan.fetch_rows(conn, "today"), "today")
    assert [(p.label, p.total_tokens) for p in points] == [("09:00", 15), ("14:00", 20)]

    # The week view still buckets by day.
    assert len(scan.series(scan.fetch_rows(conn, "week"), "week")) == 1


def test_today_is_the_local_day_not_the_utc_one(conn) -> None:
    """Work done just after local midnight belongs to TODAY.

    Bounding the window with UTC instead means a user east of Greenwich opens
    the panel each morning to "nothing recorded in this range" while the tokens
    they just spent are filed under yesterday. The regression is invisible in
    UTC+0, which is why this asserts on a fixed local wall-clock time.
    """
    conn.execute(
        "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens) VALUES"
        " ('f', 'claude-code', datetime('now', 'localtime', 'start of day', '+5 minutes', 'utc'),"
        "  'm', 42)"
    )
    conn.commit()

    rows = scan.fetch_rows(conn, "today")
    assert [row["input_tokens"] for row in rows] == [42]
    assert scan.series(rows, "today")[0].label == "00:00"


def test_previous_total_is_none_for_all_time(tmp_path: Path, conn, monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    assert registry.agents_report(conn, "all").combined.previous_total_tokens is None
    assert registry.agents_report(conn, "today").combined.previous_total_tokens == 0


def test_usage_agents_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude_root = tmp_path / "fake-claude-projects"
    proj = claude_root / "proj-a"
    proj.mkdir(parents=True)
    (proj / "session-1.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-15T09:00:00.000Z",
                "message": {
                    "model": "claude-sonnet-5",
                    "usage": {"input_tokens": 100, "output_tokens": 40},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", claude_root)
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault)))

    payload = client.get("/api/usage/agents", params={"range": "all"}).json()
    assert payload["combined"]["total_tokens"] == 140
    by_id = {agent["id"]: agent for agent in payload["agents"]}
    assert by_id["claude-code"]["detected"] is True
    assert by_id["claude-code"]["total_tokens"] == 140
    assert by_id["codex"]["detected"] is False
    assert by_id["codex"]["total_tokens"] == 0

    assert client.get("/api/usage/agents", params={"range": "nope"}).status_code == 422
