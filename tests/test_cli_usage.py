"""Tests for account-wide Claude Code CLI usage parsing and GET /api/usage/cli."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import cli_usage
from backend.cli_usage import (
    cli_usage_report,
    parse_transcript,
    scan_projects,
    sync_cli_usage,
)
from backend.config import Settings
from backend.db import connect, init_schema
from backend.main import create_app


def _assistant_line(ts: str, model: str, in_tok: int, out_tok: int, **extra_usage) -> str:
    usage = {"input_tokens": in_tok, "output_tokens": out_tok, **extra_usage}
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"model": model, "usage": usage}})


def test_scan_projects_excludes_subagents(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    proj_a = root / "proj-a"
    proj_a.mkdir(parents=True)
    (proj_a / "session-1.jsonl").write_text("", encoding="utf-8")
    (proj_a / "subagents").mkdir()
    (proj_a / "subagents" / "agent-x.jsonl").write_text("", encoding="utf-8")

    proj_b = root / "proj-b"
    proj_b.mkdir()
    (proj_b / "session-2.jsonl").write_text("", encoding="utf-8")

    found = {p.name for p in scan_projects(root)}
    assert found == {"session-1.jsonl", "session-2.jsonl"}


def test_scan_projects_missing_root_yields_nothing(tmp_path: Path) -> None:
    assert list(scan_projects(tmp_path / "does-not-exist")) == []


def test_parse_transcript_filters_and_tolerates_malformed(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    lines = [
        json.dumps({"type": "user", "timestamp": "2026-07-15T09:00:00.000Z"}),
        _assistant_line("2026-07-15T09:00:01.000Z", "claude-sonnet-5", 10, 5, cache_read_input_tokens=3),
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-15T09:00:02.000Z",
                "message": {"model": "<synthetic>", "usage": {"input_tokens": 1, "output_tokens": 1}},
            }
        ),
        "not json at all {{{",
        _assistant_line("2026-07-15T09:00:03.000Z", "claude-haiku-4-5-20251001", 4, 2),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = list(parse_transcript(path))
    assert len(rows) == 2
    assert rows[0]["model"] == "claude-sonnet-5"
    assert rows[0]["input_tokens"] == 10
    assert rows[0]["cache_read_input_tokens"] == 3
    assert rows[1]["model"] == "claude-haiku-4-5-20251001"


def test_parse_transcript_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(parse_transcript(tmp_path / "ghost.jsonl")) == []


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_sync_cli_usage_skips_unchanged_files(tmp_path: Path, conn) -> None:
    root = tmp_path / "projects"
    proj = root / "proj-a"
    proj.mkdir(parents=True)
    transcript = proj / "session-1.jsonl"
    transcript.write_text(
        _assistant_line("2026-07-15T09:00:00.000Z", "claude-sonnet-5", 10, 5) + "\n",
        encoding="utf-8",
    )

    first = sync_cli_usage(conn, root)
    assert first == 1

    second = sync_cli_usage(conn, root)
    assert second == 0, "unchanged file (same mtime/size) must not be re-parsed"

    rows = conn.execute("SELECT COUNT(*) AS n FROM cli_usage").fetchone()
    assert rows["n"] == 1


def test_sync_cli_usage_reparses_changed_files(tmp_path: Path, conn) -> None:
    root = tmp_path / "projects"
    proj = root / "proj-a"
    proj.mkdir(parents=True)
    transcript = proj / "session-1.jsonl"
    transcript.write_text(
        _assistant_line("2026-07-15T09:00:00.000Z", "claude-sonnet-5", 10, 5) + "\n",
        encoding="utf-8",
    )
    sync_cli_usage(conn, root)

    transcript.write_text(
        _assistant_line("2026-07-15T09:00:00.000Z", "claude-sonnet-5", 10, 5) + "\n"
        + _assistant_line("2026-07-15T09:05:00.000Z", "claude-sonnet-5", 20, 8) + "\n",
        encoding="utf-8",
    )
    inserted = sync_cli_usage(conn, root)
    assert inserted == 2, "a changed file is fully re-parsed"

    rows = conn.execute("SELECT COUNT(*) AS n FROM cli_usage").fetchone()
    assert rows["n"] == 2


def _seed(conn, ts: str, model: str, tokens_in: int, tokens_out: int) -> None:
    conn.execute(
        "INSERT INTO cli_usage (file_path, ts, model, input_tokens, output_tokens)"
        " VALUES ('fake.jsonl', ?, ?, ?, ?)",
        (ts, model, tokens_in, tokens_out),
    )
    conn.commit()


def test_cli_usage_report_today(tmp_path: Path, conn) -> None:
    _seed(conn, "2026-06-01 09:00:00", "claude-sonnet-5", 999, 999)  # old — excluded from "today"

    report = cli_usage_report(conn, "today", root=tmp_path / "no-such-dir")
    assert report.range == "today"
    assert report.total_tokens == 0
    assert report.models == []


def test_cli_usage_report_week_groups_by_day_and_model(tmp_path: Path, conn) -> None:
    conn.execute(
        "INSERT INTO cli_usage (file_path, ts, model, input_tokens, output_tokens) VALUES"
        " ('f', datetime('now', '-1 days'), 'claude-sonnet-5', 10, 5),"
        " ('f', datetime('now', '-1 days'), 'claude-sonnet-5', 20, 5),"
        " ('f', datetime('now'), 'claude-haiku-4-5-20251001', 30, 5),"
        " ('f', datetime('now', '-30 days'), 'claude-sonnet-5', 999, 999)"
    )
    conn.commit()

    report = cli_usage_report(conn, "week", root=tmp_path / "no-such-dir")
    assert report.total_tokens == 75, "rows older than 7 days are excluded"
    assert len(report.series) == 2, "week series is one point per day"
    models = {m.model: m.total_tokens for m in report.models}
    assert models == {"claude-sonnet-5": 40, "claude-haiku-4-5-20251001": 35}


def test_cli_usage_report_all_groups_by_week(tmp_path: Path, conn) -> None:
    _seed(conn, "2026-06-01 09:00:00", "claude-sonnet-5", 10, 0)
    _seed(conn, "2026-06-02 09:00:00", "claude-sonnet-5", 20, 0)
    _seed(conn, "2026-07-14 09:00:00", "claude-sonnet-5", 40, 0)

    report = cli_usage_report(conn, "all", root=tmp_path / "no-such-dir")
    assert report.total_tokens == 70
    assert len(report.series) == 2, "all-time series is one point per week"


def test_usage_cli_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_root = tmp_path / "fake-claude-projects"
    proj = fake_root / "proj-a"
    proj.mkdir(parents=True)
    (proj / "session-1.jsonl").write_text(
        _assistant_line("2026-07-15T09:00:00.000Z", "claude-sonnet-5", 100, 40) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_usage, "DEFAULT_CLAUDE_HOME", fake_root)

    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault)))

    payload = client.get("/api/usage/cli", params={"range": "all"}).json()
    assert payload["total_tokens"] == 140
    assert payload["models"][0]["model"] == "claude-sonnet-5"

    assert client.get("/api/usage/cli", params={"range": "nope"}).status_code == 422
