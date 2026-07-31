"""Tests for folder discovery, custom agent sources, and their endpoints."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.main import create_app
from backend.telemetry import claude_cli, scan
from backend.telemetry.agents import discover as discovery
from backend.telemetry.agents import registry, store
from backend.telemetry.agents.codex import CodexSource


def _assistant(ts: str, model: str, tin: int, tout: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {"model": model, "usage": {"input_tokens": tin, "output_tokens": tout}},
        }
    )


@pytest.fixture()
def vault(tmp_path: Path) -> Settings:
    root = tmp_path / "vault"
    root.mkdir()
    return Settings(_vault_path=root)


@pytest.fixture()
def client(vault: Settings, monkeypatch, tmp_path: Path) -> TestClient:
    # Keep the real machine's agents out of every assertion.
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")
    return TestClient(create_app(vault))


# --- discovery ------------------------------------------------------------


def test_discover_recognises_claude_jsonl(tmp_path: Path) -> None:
    logs = tmp_path / "logs" / "proj"
    logs.mkdir(parents=True)
    (logs / "a.jsonl").write_text(
        _assistant("2026-07-20T09:00:00.000Z", "gemini-2.5-pro", 100, 40) + "\n"
        + _assistant("2026-07-20T10:00:00.000Z", "gemini-2.5-pro", 10, 4) + "\n",
        encoding="utf-8",
    )

    found = discovery.discover(tmp_path / "logs")
    assert found.ok
    assert found.fmt is not None and found.fmt.id == "claude-jsonl"
    assert found.turns == 2
    assert found.total_tokens == 154
    assert found.models == ["gemini-2.5-pro"]
    assert found.first_ts == "2026-07-20T09:00:00.000Z"
    assert found.last_ts == "2026-07-20T10:00:00.000Z"


def test_discover_reports_a_reason_when_nothing_matches(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert discovery.discover(empty).ok is False

    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "notes.jsonl").write_text('{"hello": "world"}\n', encoding="utf-8")
    found = discovery.discover(junk)
    assert found.ok is False
    assert "token counts" in found.detail, "an unrecognised folder must say why"


def test_discover_rejects_missing_and_non_directories(tmp_path: Path) -> None:
    assert "does not exist" in discovery.discover(tmp_path / "ghost").detail
    afile = tmp_path / "a.jsonl"
    afile.write_text("", encoding="utf-8")
    assert "not a folder" in discovery.discover(afile).detail


def test_discover_marks_partial_reads_as_sampled(tmp_path: Path) -> None:
    logs = tmp_path / "many"
    logs.mkdir()
    for index in range(discovery.STAT_FILES + 5):
        (logs / f"s{index}.jsonl").write_text(
            _assistant("2026-07-20T09:00:00.000Z", "m", 10, 1) + "\n", encoding="utf-8"
        )

    found = discovery.discover(logs)
    assert found.ok and found.sampled is True
    assert found.files == discovery.STAT_FILES + 5
    assert found.turns == discovery.STAT_FILES, "stats stop at the cap"
    assert f"of {found.files} files" in found.detail


def test_expand_handles_home_and_nonsense() -> None:
    assert str(discovery.expand("~")) != "~"
    assert str(discovery.expand("  ")) in ("", ".")


# --- store ----------------------------------------------------------------


def test_slugify_and_reserved_ids() -> None:
    assert store.slugify("Gemini CLI") == "gemini-cli"
    assert store.slugify("  !!!  ") == "agent"
    assert "claude-subagents" in store.RESERVED_IDS


def test_load_sources_tolerates_missing_and_corrupt(tmp_path: Path) -> None:
    assert store.load_sources(tmp_path / "absent.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{{{", encoding="utf-8")
    assert store.load_sources(bad) == []
    # Corrupt is set aside, not left in place for the next save to overwrite.
    assert not bad.exists()
    assert (tmp_path / "bad.json.corrupt").read_text(encoding="utf-8") == "{{{"


def test_saved_sources_survive_a_corrupt_read(tmp_path: Path) -> None:
    """The old code's data-loss path: load [] -> append -> save, wiping the file."""
    path = tmp_path / "agent-sources.json"
    path.write_text('[{"id": "gemini-cli", "path', encoding="utf-8")  # truncated write

    assert store.load_sources(path) == []
    store.save_sources(path, [{"id": "new", "path": "/logs"}])

    assert store.load_sources(path) == [{"id": "new", "path": "/logs"}]
    quarantined = (tmp_path / "agent-sources.json.corrupt").read_text(encoding="utf-8")
    assert "gemini-cli" in quarantined  # the user's entry is still recoverable


# --- endpoints ------------------------------------------------------------


def test_scan_endpoint(client: TestClient, tmp_path: Path) -> None:
    logs = tmp_path / "scanme"
    logs.mkdir()
    (logs / "s.jsonl").write_text(
        _assistant("2026-07-20T09:00:00.000Z", "gpt-x", 100, 40) + "\n", encoding="utf-8"
    )

    payload = client.post("/api/usage/agents/scan", json={"path": str(logs)}).json()
    assert payload["ok"] is True
    assert payload["format"] == "claude-jsonl"
    assert payload["total_tokens"] == 140
    assert payload["models"] == ["gpt-x"]

    missing = client.post("/api/usage/agents/scan", json={"path": str(tmp_path / "ghost")}).json()
    assert missing["ok"] is False and missing["detail"]

    assert client.post("/api/usage/agents/scan", json={"path": "  "}).status_code == 422


def test_custom_agent_round_trip(client: TestClient, vault: Settings, tmp_path: Path) -> None:
    logs = tmp_path / "gem"
    logs.mkdir()
    (logs / "s.jsonl").write_text(
        _assistant("2026-07-20T09:00:00.000Z", "gemini-2.5-pro", 1_000_000, 100_000) + "\n",
        encoding="utf-8",
    )

    created = client.post(
        "/api/usage/agents/custom", json={"name": "Gemini CLI", "path": str(logs)}
    )
    assert created.status_code == 201
    assert created.json()["id"] == "gemini-cli"
    assert created.json()["format"] == "claude-jsonl"

    report = client.get("/api/usage/agents", params={"range": "all"}).json()
    by_id = {a["id"]: a for a in report["agents"]}
    assert by_id["gemini-cli"]["total_tokens"] == 1_100_000
    assert by_id["gemini-cli"]["detected"] is True
    assert by_id["gemini-cli"]["builtin"] is False
    assert report["combined"]["total_tokens"] == 1_100_000

    # A user-registered source has no rate table, so it is named rather than priced.
    assert by_id["gemini-cli"]["estimated_cost_usd"] == 0.0
    assert by_id["gemini-cli"]["unpriced_models"] == ["gemini-2.5-pro"]

    deleted = client.delete("/api/usage/agents/custom/gemini-cli")
    assert deleted.status_code == 200
    assert deleted.json()["rows_removed"] == 1

    after = client.get("/api/usage/agents", params={"range": "all"}).json()
    assert [a["id"] for a in after["agents"]] == ["claude-code", "claude-subagents", "codex"]
    assert after["combined"]["total_tokens"] == 0, "deleting a source purges its rows"

    assert client.delete("/api/usage/agents/custom/gemini-cli").status_code == 404


def test_custom_agent_rejects_reserved_and_duplicate_names(
    client: TestClient, tmp_path: Path
) -> None:
    logs = tmp_path / "dup"
    logs.mkdir()
    (logs / "s.jsonl").write_text(
        _assistant("2026-07-20T09:00:00.000Z", "m", 10, 1) + "\n", encoding="utf-8"
    )

    reserved = client.post("/api/usage/agents/custom", json={"name": "Codex", "path": str(logs)})
    assert reserved.status_code == 409
    assert "built-in" in reserved.json()["detail"]

    mine = {"name": "Mine", "path": str(logs)}
    assert client.post("/api/usage/agents/custom", json=mine).status_code == 201
    assert client.post("/api/usage/agents/custom", json=mine).status_code == 409


def test_custom_agent_rejects_an_unreadable_folder(client: TestClient, tmp_path: Path) -> None:
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "x.jsonl").write_text('{"nope": 1}\n', encoding="utf-8")

    response = client.post("/api/usage/agents/custom", json={"name": "Junk", "path": str(junk)})
    assert response.status_code == 422, "a folder with no usage must not become an agent"


def test_formats_endpoint_names_what_it_can_read(client: TestClient) -> None:
    formats = client.get("/api/usage/agents/formats").json()
    assert {f["id"] for f in formats} == {"claude-jsonl", "codex-rollout"}
    assert all(f["label"] and f["blurb"] for f in formats)


# --- the (path, agent) key ------------------------------------------------


OLD_SCAN_TABLE = """
CREATE TABLE cli_usage_files (
    path       TEXT PRIMARY KEY,
    agent      TEXT NOT NULL DEFAULT 'claude-code',
    mtime_ns   INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_scan_key_migration_rebuilds_and_keeps_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(OLD_SCAN_TABLE)
    raw.execute(
        "INSERT INTO cli_usage_files (path, agent, mtime_ns, size)"
        " VALUES ('a.jsonl', 'claude-code', 1, 2)"
    )
    raw.commit()
    raw.close()

    conn = connect(db_path)
    init_schema(conn)
    try:
        keys = {r["name"] for r in conn.execute("PRAGMA table_info(cli_usage_files)") if r["pk"]}
        assert keys == {"path", "agent"}
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage_files").fetchone()["n"] == 1

        # The whole point: the same file may now be claimed by two agents.
        conn.execute(
            "INSERT INTO cli_usage_files (path, agent, mtime_ns, size)"
            " VALUES ('a.jsonl', 'claude-subagents', 1, 2)"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage_files").fetchone()["n"] == 2
    finally:
        conn.close()


def test_two_sources_over_the_same_file_do_not_evict_each_other(tmp_path: Path) -> None:
    """The bug the re-key exists to prevent, exercised directly."""
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    try:
        shared = tmp_path / "shared.jsonl"
        shared.write_text(
            _assistant("2026-07-20T09:00:00.000Z", "m", 10, 1) + "\n", encoding="utf-8"
        )

        def parse(path: Path):
            from backend.telemetry.formats import parse_claude_jsonl

            return parse_claude_jsonl(path)

        assert scan.sync_rows(conn, "agent-a", [shared], parse) == 1
        assert scan.sync_rows(conn, "agent-b", [shared], parse) == 1

        # Re-scanning A must still be a no-op — B did not overwrite A's mark.
        assert scan.sync_rows(conn, "agent-a", [shared], parse) == 0

        counts = {
            row["agent"]: row["n"]
            for row in conn.execute("SELECT agent, COUNT(*) n FROM cli_usage GROUP BY agent")
        }
        assert counts == {"agent-a": 1, "agent-b": 1}
    finally:
        conn.close()


def test_vanished_transcripts_are_reaped(tmp_path: Path) -> None:
    """Agent CLIs rotate old sessions; their tokens must not be counted forever."""
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    try:
        from backend.telemetry.formats import parse_claude_jsonl

        keep = tmp_path / "keep.jsonl"
        drop = tmp_path / "drop.jsonl"
        for path in (keep, drop):
            path.write_text(
                _assistant("2026-07-20T09:00:00.000Z", "m", 10, 1) + "\n", encoding="utf-8"
            )

        assert scan.sync_rows(conn, "a", [keep, drop], parse_claude_jsonl) == 2

        drop.unlink()
        scan.sync_rows(conn, "a", [keep], parse_claude_jsonl)

        remaining = [r["file_path"] for r in conn.execute("SELECT file_path FROM cli_usage")]
        assert remaining == [str(keep)]
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage_files").fetchone()["n"] == 1
    finally:
        conn.close()


def test_an_empty_scan_never_wipes_history(tmp_path: Path) -> None:
    """A temporarily unreachable folder must not be read as "delete everything"."""
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    try:
        from backend.telemetry.formats import parse_claude_jsonl

        log = tmp_path / "s.jsonl"
        log.write_text(_assistant("2026-07-20T09:00:00.000Z", "m", 10, 1) + "\n", encoding="utf-8")
        assert scan.sync_rows(conn, "a", [log], parse_claude_jsonl) == 1

        # The root went away — an unplugged drive, not a deliberate purge.
        scan.sync_rows(conn, "a", [], parse_claude_jsonl)
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage").fetchone()["n"] == 1
    finally:
        conn.close()


def test_purge_agent_removes_rows_and_scan_state(tmp_path: Path) -> None:
    conn = connect(tmp_path / "argus.db")
    init_schema(conn)
    try:
        conn.execute(
            "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens)"
            " VALUES ('f', 'gone', '2026-07-20 09:00:00', 'm', 5)"
        )
        conn.execute(
            "INSERT INTO cli_usage_files (path, agent, mtime_ns, size) VALUES ('f', 'gone', 1, 2)"
        )
        conn.commit()

        assert scan.purge_agent(conn, "gone") == 1
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM cli_usage_files").fetchone()["n"] == 0
    finally:
        conn.close()


def test_orphan_rows_are_kept_out_of_the_combined_total(tmp_path: Path, monkeypatch) -> None:
    """A row whose agent is gone must not inflate ALL with nothing to explain it."""
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(_vault_path=root)
    monkeypatch.setattr(claude_cli, "DEFAULT_CLAUDE_HOME", tmp_path / "no-claude")
    monkeypatch.setattr(CodexSource, "root", lambda self: tmp_path / "no-codex")

    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        conn.execute(
            "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens)"
            " VALUES ('f', 'deregistered', datetime('now'), 'm', 999)"
        )
        conn.commit()
        report = registry.agents_report(conn, "today", settings)
        assert report.combined.total_tokens == 0
    finally:
        conn.close()
