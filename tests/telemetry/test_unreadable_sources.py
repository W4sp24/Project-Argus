"""Transcripts we found but could not read must say so, not report zero.

The Codex parser has never been run against a real ``~/.codex`` — its fixtures
were written from the same assumption as the parser itself, so a wrong guess
about the format is self-consistent and invisible. Worse, the parsers skip
lines they do not recognise, so a format change degrades to a confident,
permanent zero rather than an error anyone would notice.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.telemetry.agents.base import AgentSource
from backend.telemetry.agents.registry import agents_report


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


class _UnreadableSource(AgentSource):
    """Installed, has transcripts, and the parser understands none of them."""

    id = "codex"
    label = "Codex"
    install_hint = "Run a Codex CLI session."
    format_id = "codex-rollout"

    def __init__(self, root: Path) -> None:
        self._root = root

    def root(self) -> Path:
        return self._root

    def transcripts(self):
        return iter([self._root / "rollout-1.jsonl"])

    def parse(self, _path: Path):
        return iter(())  # every line looked like noise


class _AbsentSource(_UnreadableSource):
    def transcripts(self):
        return iter(())


def _slice(report, agent_id: str):
    return next(agent for agent in report.agents if agent.id == agent_id)


def test_found_transcripts_that_parse_to_nothing_are_flagged(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    (root / "rollout-1.jsonl").write_text('{"unexpected": "shape"}\n', encoding="utf-8")
    source = _UnreadableSource(root)
    monkeypatch.setattr(
        "backend.telemetry.agents.registry.all_sources", lambda _settings=None: [source]
    )

    report = agents_report(conn, "week")

    codex = _slice(report, "codex")
    assert codex.total_tokens == 0
    assert codex.unreadable is not None, "a zero we cannot trust must not look like a real zero"
    assert "could not read" in codex.unreadable
    assert str(root) in codex.unreadable


def test_an_installed_agent_with_no_sessions_yet_is_not_flagged(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    """Nothing to read is an honest zero — only unreadable files earn a warning."""
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        "backend.telemetry.agents.registry.all_sources",
        lambda _settings=None: [_AbsentSource(root)],
    )

    report = agents_report(conn, "week")

    assert _slice(report, "codex").unreadable is None


def test_an_uninstalled_agent_is_not_flagged(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.telemetry.agents.registry.all_sources",
        lambda _settings=None: [_UnreadableSource(tmp_path / "nope")],
    )

    report = agents_report(conn, "week")

    codex = _slice(report, "codex")
    assert codex.detected is False
    assert codex.unreadable is None, "not installed is already explained by the install hint"
