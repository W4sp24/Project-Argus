"""Insights: how the user is actually doing, computed from vault + db.

Everything here is read-only and local: ✅ done-dates scanned from markdown,
open tasks from the cache, exam attempts from SQLite, calendar load from the
gcal connector (zeros when unconfigured).
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

from backend.config import Settings
from backend.connectors import gcal
from backend.rag.paths import EXCLUDED_TOP_DIRS
from backend.tasks.parser import refresh_cache

DONE_DATE_RE = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")
TREND_DAYS = 14
CALENDAR_DAYS = 7
FOCUS_BUDGET_HOURS = 8.0
HEATMAP_DAYS = 371  # 53 weeks — a GitHub-style year grid
CAPTURE_STAMP_RE = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")


class CourseScores(BaseModel):
    course: str
    attempts: list[dict[str, Any]]  # {date, pct}, chronological


class StudyInsights(BaseModel):
    streak_days: int
    courses: list[CourseScores]


class InsightsSummary(BaseModel):
    completion_trend: list[dict[str, Any]]  # {date, completed}
    overdue: list[dict[str, Any]]  # {date, count}
    calendar: list[dict[str, Any]]  # {date, event_hours, focus_hours}
    study: StudyInsights
    configured: dict[str, bool]


def _completions_by_day(settings: Settings) -> dict[str, int]:
    counts: dict[str, int] = {}
    for file_path in settings.vault_path.rglob("*.md"):
        relative = file_path.relative_to(settings.vault_path)
        if any(part in EXCLUDED_TOP_DIRS for part in relative.parts):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for done_date in DONE_DATE_RE.findall(text):
            counts[done_date] = counts.get(done_date, 0) + 1
    return counts


def _event_hours(day: date) -> float:
    hours = 0.0
    for event in gcal.list_events(day):
        if event.all_day:
            continue
        try:
            start = datetime.fromisoformat(event.start)
            end = datetime.fromisoformat(event.end)
        except ValueError:
            continue
        hours += max(0.0, (end - start).total_seconds() / 3600)
    return round(hours, 2)


def _study(conn: sqlite3.Connection) -> tuple[list[CourseScores], set[str]]:
    rows = conn.execute(
        "SELECT exams.course AS course, attempts.score, attempts.total, attempts.created_at"
        " FROM attempts JOIN exams ON exams.id = attempts.exam_id"
        " ORDER BY attempts.created_at"
    ).fetchall()
    by_course: dict[str, list[dict[str, Any]]] = {}
    activity_days: set[str] = set()
    for row in rows:
        day = row["created_at"][:10]
        activity_days.add(day)
        pct = round(100 * row["score"] / row["total"]) if row["total"] else 0
        by_course.setdefault(row["course"], []).append({"date": day, "pct": pct})
    courses = [CourseScores(course=c, attempts=a) for c, a in sorted(by_course.items())]
    return courses, activity_days


def _streak(activity_days: set[str], today: date) -> int:
    streak = 0
    cursor = today
    while cursor.isoformat() in activity_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def insights_summary(
    settings: Settings, conn: sqlite3.Connection, today: date | None = None
) -> InsightsSummary:
    today = today or date.today()
    refresh_cache(conn, settings.vault_path)

    completions = _completions_by_day(settings)
    window = [(today - timedelta(days=i)).isoformat() for i in range(TREND_DAYS - 1, -1, -1)]
    trend = [{"date": day, "completed": completions.get(day, 0)} for day in window]

    overdue_counts: dict[str, int] = {}
    floor = (today - timedelta(days=TREND_DAYS)).isoformat()
    for row in conn.execute(
        "SELECT due, COUNT(*) AS n FROM tasks_cache"
        " WHERE done = 0 AND due IS NOT NULL AND due < ? AND due >= ? GROUP BY due",
        (today.isoformat(), floor),
    ):
        overdue_counts[row["due"]] = row["n"]
    overdue = [{"date": day, "count": n} for day, n in sorted(overdue_counts.items())]

    calendar = []
    for offset in range(CALENDAR_DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        event_hours = _event_hours(day)
        calendar.append(
            {
                "date": day.isoformat(),
                "event_hours": event_hours,
                "focus_hours": round(max(0.0, FOCUS_BUDGET_HOURS - event_hours), 2),
            }
        )

    courses, attempt_days = _study(conn)
    activity_days = attempt_days | {day for day, count in completions.items() if count}

    return InsightsSummary(
        completion_trend=trend,
        overdue=overdue,
        calendar=calendar,
        study=StudyInsights(streak_days=_streak(activity_days, today), courses=courses),
        configured={"gcal": gcal.configured()},
    )


class HeatmapDay(BaseModel):
    date: str
    total: int
    tasks: int
    notes: int
    study: int
    captures: int


class HeatmapResponse(BaseModel):
    days: list[HeatmapDay]


def _note_touches_by_day(settings: Settings) -> dict[str, int]:
    """Notes created/edited per day, from the vault's git history (I2 keeps it rich)."""
    result = subprocess.run(
        ["git", "log", f"--since={HEATMAP_DAYS + 1} days ago", "--date=short",
         "--pretty=format:@%ad", "--name-only"],
        cwd=settings.vault_path, capture_output=True, text=True, check=False,
    )
    counts: dict[str, int] = {}
    day: str | None = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("@"):
            day = line[1:]
            continue
        if not line or day is None:
            continue
        path = PurePosixPath(line)
        if path.suffix != ".md" or any(part in EXCLUDED_TOP_DIRS for part in path.parts):
            continue
        counts[day] = counts.get(day, 0) + 1
    return counts


def _captures_by_day(settings: Settings) -> dict[str, int]:
    counts: dict[str, int] = {}
    inbox = settings.vault_path / "00-Inbox"
    if inbox.is_dir():
        for note in inbox.glob("*.md"):
            try:
                text = note.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for stamp in CAPTURE_STAMP_RE.findall(text):
                counts[stamp] = counts.get(stamp, 0) + 1
    return counts


def _study_by_day(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT created_at FROM attempts"):
        day = row["created_at"][:10]
        counts[day] = counts.get(day, 0) + 1
    return counts


def heatmap_summary(
    settings: Settings, conn: sqlite3.Connection, today: date | None = None
) -> HeatmapResponse:
    today = today or date.today()
    tasks = _completions_by_day(settings)
    notes = _note_touches_by_day(settings)
    study = _study_by_day(conn)
    captures = _captures_by_day(settings)
    days: list[HeatmapDay] = []
    for offset in range(HEATMAP_DAYS - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        t, n, s, c = tasks.get(day, 0), notes.get(day, 0), study.get(day, 0), captures.get(day, 0)
        days.append(
            HeatmapDay(date=day, total=t + n + s + c, tasks=t, notes=n, study=s, captures=c)
        )
    return HeatmapResponse(days=days)
