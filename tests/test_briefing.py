"""Tests for the morning briefing: data assembly, rendering, composition."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.briefing import briefing_data, compose_briefing, render_briefing
from backend.config import Settings
from backend.db import connect, init_schema

TODAY = date(2026, 7, 13)
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
IN_THREE_DAYS = (TODAY + timedelta(days=3)).isoformat()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10-Daily").mkdir(parents=True)
    (root / "10-Daily" / f"{YESTERDAY}.md").write_text(
        f"# {YESTERDAY}\n\n- [ ] Email the registrar\n- [x] Ship P3\n",
        encoding="utf-8",
    )
    (root / "20-Projects").mkdir()
    (root / "20-Projects" / "plans.md").write_text(
        f"# Plans\n\n- [ ] Submit lab report 📅 {TODAY.isoformat()}\n"
        f"- [ ] Renew passport 📅 2026-07-01\n"
        f"- [ ] ES101 midterm exam 📅 {IN_THREE_DAYS} #es101\n",
        encoding="utf-8",
    )
    queue_dir = root / "15-Courses" / "ES101" / "study"
    queue_dir.mkdir(parents=True)
    (queue_dir / "review-queue.md").write_text(
        "# Review queue\n\n- [ ] Plate boundaries (p.4)\n- [x] Convection cells (p.6)\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "friday.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_briefing_data_buckets_tasks_correctly(vault: Path, conn: sqlite3.Connection) -> None:
    data = briefing_data(Settings(_vault_path=vault), conn, today=TODAY)

    assert data.date == TODAY.isoformat()
    assert any("Submit lab report" in task.text for task in data.due_today)
    assert any("Renew passport" in task.text for task in data.overdue)
    assert data.yesterday_unfinished == ["Email the registrar"]
    assert data.events == []  # gcal unconfigured degrades to empty


def test_briefing_data_exam_countdown_and_weak_topics(
    vault: Path, conn: sqlite3.Connection
) -> None:
    data = briefing_data(Settings(_vault_path=vault), conn, today=TODAY)

    assert len(data.exam_countdowns) == 1
    countdown = data.exam_countdowns[0]
    assert "midterm" in countdown["title"].lower()
    assert countdown["due"] == IN_THREE_DAYS
    assert countdown["days_left"] == 3

    assert data.weak_topics == ["Plate boundaries (p.4)"]


def test_render_briefing_includes_and_omits_sections(vault: Path, conn: sqlite3.Connection) -> None:
    data = briefing_data(Settings(_vault_path=vault), conn, today=TODAY)
    markdown = render_briefing(data)

    assert "Submit lab report" in markdown
    assert "Renew passport" in markdown
    assert "Email the registrar" in markdown
    assert "3 day" in markdown  # exam countdown
    assert "Plate boundaries" in markdown
    assert "Schedule" not in markdown  # no calendar events -> section omitted


def test_compose_briefing_falls_back_when_composer_fails(
    vault: Path, conn: sqlite3.Connection
) -> None:
    def broken_composer(_data) -> str:
        raise RuntimeError("agent unavailable")

    markdown = compose_briefing(
        Settings(_vault_path=vault), conn, composer=broken_composer, today=TODAY
    )
    assert "Submit lab report" in markdown  # deterministic fallback


def test_compose_briefing_uses_composer_output(vault: Path, conn: sqlite3.Connection) -> None:
    markdown = compose_briefing(
        Settings(_vault_path=vault),
        conn,
        composer=lambda data: f"Good morning! {len(data.overdue)} overdue.",
        today=TODAY,
    )
    assert markdown == "Good morning! 1 overdue."
