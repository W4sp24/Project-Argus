"""Tests for the insights summary: trends, streaks, and score history."""

import json
import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db import connect, init_schema
from backend.insights import HEATMAP_DAYS, heatmap_summary, insights_summary
from backend.main import create_app

TODAY = date(2026, 7, 13)
D = {i: (TODAY - timedelta(days=i)).isoformat() for i in range(15)}


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    (root / "20-Projects" / "log.md").write_text(
        f"# Log\n\n"
        f"- [x] Shipped the thing ✅ {D[0]}\n"
        f"- [x] Reviewed notes ✅ {D[0]}\n"
        f"- [x] Older win ✅ {D[1]}\n"
        f"- [ ] Slipped task 📅 {D[2]}\n"
        f"- [ ] Future task 📅 {(TODAY + timedelta(days=2)).isoformat()}\n",
        encoding="utf-8",
    )
    (root / "99-Private").mkdir()
    (root / "99-Private" / "secret.md").write_text(f"- [x] Hidden ✅ {D[0]}\n", encoding="utf-8")
    return root


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "friday.db")
    init_schema(connection)
    exam = connection.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES ('ES101', 'CM8', '[]')"
    ).lastrowid
    connection.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (?, 3, 10, ?, ?)",
        (exam, json.dumps([]), f"{D[1]} 20:00:00"),
    )
    connection.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (?, 8, 10, ?, ?)",
        (exam, json.dumps([]), f"{D[0]} 09:00:00"),
    )
    connection.commit()
    yield connection
    connection.close()


def test_completion_trend_counts_done_dates_excluding_private(
    vault: Path, conn: sqlite3.Connection
) -> None:
    summary = insights_summary(Settings(_vault_path=vault), conn, today=TODAY)

    trend = {row["date"]: row["completed"] for row in summary.completion_trend}
    assert len(summary.completion_trend) == 14
    assert trend[D[0]] == 2  # 99-Private completion NOT counted (I3)
    assert trend[D[1]] == 1
    assert trend[D[5]] == 0


def test_overdue_and_calendar_degrade_without_gcal(vault: Path, conn: sqlite3.Connection) -> None:
    summary = insights_summary(Settings(_vault_path=vault), conn, today=TODAY)

    overdue = {row["date"]: row["count"] for row in summary.overdue}
    assert overdue.get(D[2]) == 1
    assert D[0] not in overdue  # due today is not overdue

    assert len(summary.calendar) == 7
    assert all(row["event_hours"] == 0 for row in summary.calendar)
    assert summary.configured == {"gcal": False}


def test_study_scores_and_streak(vault: Path, conn: sqlite3.Connection) -> None:
    summary = insights_summary(Settings(_vault_path=vault), conn, today=TODAY)

    assert summary.study.streak_days == 2  # activity today (✅+attempt) and yesterday
    courses = {course.course: course for course in summary.study.courses}
    assert list(courses) == ["ES101"]
    pcts = [attempt["pct"] for attempt in courses["ES101"].attempts]
    assert pcts == [30, 80]  # chronological


def test_insights_endpoint(vault: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(_vault_path=vault), briefing_composer=lambda data: "x"))
    payload = client.get("/api/insights").json()
    assert len(payload["completion_trend"]) == 14
    assert payload["configured"] == {"gcal": False}


@pytest.fixture()
def settings_and_conn(tmp_path: Path) -> tuple[Settings, sqlite3.Connection]:
    """A clean tmp vault + initialized db, for tests that build their own vault content."""
    vault_dir = tmp_path / "hm_vault"
    vault_dir.mkdir()
    settings = Settings(_vault_path=vault_dir)
    connection = connect(tmp_path / "hm.db")
    init_schema(connection)
    yield settings, connection
    connection.close()


def test_heatmap_counts_tasks_notes_study_captures(settings_and_conn):
    settings, conn = settings_and_conn  # adapt name to the file's existing fixture
    vault = settings.vault_path
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault, capture_output=True)

    today = date(2026, 7, 13)
    (vault / "20-Projects").mkdir(exist_ok=True)
    (vault / "20-Projects" / "p.md").write_text(
        "- [x] done ✅ 2026-07-13\n- [x] older ✅ 2026-07-10\n", encoding="utf-8"
    )
    (vault / "00-Inbox").mkdir(exist_ok=True)
    (vault / "00-Inbox" / "capture-2026-07-13.md").write_text(
        "- [ ] captured thing ➕ 2026-07-13\n", encoding="utf-8"
    )
    conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES ('CS', 'T', '[]')"
    )
    conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (1, 8, 10, '[]', '2026-07-13 10:00:00')"
    )
    conn.commit()

    result = heatmap_summary(settings, conn, today=today)
    assert len(result.days) == HEATMAP_DAYS
    assert result.days[-1].date == "2026-07-13"
    latest = result.days[-1]
    assert latest.tasks == 1
    assert latest.captures == 1
    assert latest.study == 1
    assert latest.total == latest.tasks + latest.notes + latest.study + latest.captures
    by_date = {d.date: d for d in result.days}
    assert by_date["2026-07-10"].tasks == 1


def test_heatmap_excludes_private(settings_and_conn):
    settings, conn = settings_and_conn
    vault = settings.vault_path
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    (vault / "99-Private").mkdir(exist_ok=True)
    (vault / "99-Private" / "secret.md").write_text(
        "- [x] secret ✅ 2026-07-13\n", encoding="utf-8"
    )
    result = heatmap_summary(settings, conn, today=date(2026, 7, 13))
    assert result.days[-1].tasks == 0
