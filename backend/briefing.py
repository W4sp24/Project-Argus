"""The morning briefing: what today looks like, assembled from the vault.

Data assembly and rendering are deterministic; an optional ``composer``
(the agent, in production) may rewrite the briefing as prose, and any
composer failure falls back to the deterministic render so 07:00 never
produces nothing.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.config import Settings
from backend.connectors import gcal
from backend.connectors.gcal import CalendarEvent
from backend.tasks.parser import TaskItem, bucketed_tasks, parse_task_line, refresh_cache

EXAM_RE = re.compile(r"\b(exam|quiz|midterm|final)\b", re.IGNORECASE)
MAX_WEAK_TOPICS = 5
UNCHECKED_RE = re.compile(r"^\s*[-*]\s+\[ \]\s+(.*)$")

Composer = Callable[["BriefingData"], str]


class BriefingData(BaseModel):
    """Everything the 07:00 briefing talks about."""

    date: str
    events: list[CalendarEvent]
    due_today: list[TaskItem]
    overdue: list[TaskItem]
    yesterday_unfinished: list[str]
    exam_countdowns: list[dict[str, Any]]
    weak_topics: list[str]


def _yesterday_unfinished(vault_path: Path, today: date) -> list[str]:
    note = vault_path / "10-Daily" / f"{(today - timedelta(days=1)).isoformat()}.md"
    if not note.is_file():
        return []
    unfinished = []
    for line in note.read_text(encoding="utf-8").splitlines():
        task = parse_task_line(line)
        if task is not None and not task.done:
            unfinished.append(task.text)
    return unfinished


def _exam_countdowns(buckets: dict[str, list[TaskItem]], today: date) -> list[dict[str, Any]]:
    countdowns = []
    for task in buckets["today"] + buckets["week"] + buckets["someday"]:
        haystack = task.text + " " + " ".join(task.tags)
        if task.due and EXAM_RE.search(haystack):
            days_left = (date.fromisoformat(task.due) - today).days
            countdowns.append({"title": task.text, "due": task.due, "days_left": days_left})
    return sorted(countdowns, key=lambda item: item["days_left"])


def _weak_topics(vault_path: Path) -> list[str]:
    topics = []
    for queue_file in sorted(vault_path.glob("15-Courses/*/study/review-queue.md")):
        for line in queue_file.read_text(encoding="utf-8").splitlines():
            match = UNCHECKED_RE.match(line)
            if match:
                topics.append(match.group(1).strip())
    return topics[:MAX_WEAK_TOPICS]


def briefing_data(settings: Settings, conn: sqlite3.Connection, today: date) -> BriefingData:
    """Assemble the day's facts. Connectors degrade to empty when unconfigured."""
    vault = settings.vault_path
    refresh_cache(conn, vault)
    buckets = bucketed_tasks(conn, today=today)
    return BriefingData(
        date=today.isoformat(),
        events=gcal.list_events(today),
        due_today=buckets["today"],
        overdue=buckets["overdue"],
        yesterday_unfinished=_yesterday_unfinished(vault, today),
        exam_countdowns=_exam_countdowns(buckets, today),
        weak_topics=_weak_topics(vault),
    )


def render_briefing(data: BriefingData) -> str:
    """Deterministic markdown briefing; empty sections are omitted."""
    parts: list[str] = []
    if data.events:
        lines = [
            f"- {event.start[-5:] if not event.all_day else 'all day'} {event.title}"
            for event in data.events
        ]
        parts.append("**Schedule**\n" + "\n".join(lines))
    if data.due_today:
        parts.append("**Due today**\n" + "\n".join(f"- {t.text}" for t in data.due_today))
    if data.overdue:
        parts.append(
            "**Overdue**\n" + "\n".join(f"- {t.text} (was due {t.due})" for t in data.overdue)
        )
    if data.yesterday_unfinished:
        parts.append(
            "**Carried over from yesterday**\n"
            + "\n".join(f"- {text}" for text in data.yesterday_unfinished)
        )
    if data.exam_countdowns:
        parts.append(
            "**Exam countdown**\n"
            + "\n".join(
                f"- {item['title']} — {item['days_left']} day(s) left ({item['due']})"
                for item in data.exam_countdowns
            )
        )
    if data.weak_topics:
        parts.append(
            "**Weak topics to review**\n" + "\n".join(f"- {topic}" for topic in data.weak_topics)
        )
    if not parts:
        parts.append("Nothing scheduled, nothing due — a clean slate.")
    return f"Briefing for {data.date}\n\n" + "\n\n".join(parts)


def agent_composer(data: BriefingData) -> str:
    """The production composer: one tool-less opus pass over the day's facts."""
    import asyncio

    from backend.agent.generate import agent_generate

    prompt = (
        "You are Argus writing the user's morning briefing for their daily note.\n"
        "Rewrite the facts below as a short, warm, scannable markdown briefing "
        "(bold section labels, bullet lists, no H1/H2 headings). Do not invent "
        "events or tasks; keep every date exactly as given.\n\n"
        f"FACTS:\n{render_briefing(data)}\n\nDATA (JSON):\n{data.model_dump_json(indent=1)}"
    )
    text = asyncio.run(agent_generate(prompt, feature="briefing")).strip()
    if not text:
        raise RuntimeError("composer returned empty text")
    return text


def compose_briefing(
    settings: Settings,
    conn: sqlite3.Connection,
    composer: Composer | None = None,
    today: date | None = None,
) -> str:
    """Build the briefing, letting ``composer`` write prose when available."""
    resolved_today = today or date.today()
    data = briefing_data(settings, conn, resolved_today)
    if composer is not None:
        from backend.audit import log_prompt_conn

        yesterday_note = f"10-Daily/{(resolved_today - timedelta(days=1)).isoformat()}.md"
        queue_paths = [
            path.relative_to(settings.vault_path).as_posix()
            for path in settings.vault_path.glob("15-Courses/*/study/review-queue.md")
        ]
        log_prompt_conn(conn, "briefing", "claude-opus-4-8", [yesterday_note, *queue_paths])
        try:
            return composer(data)
        except Exception:  # a dead agent must never kill the 07:00 job
            pass
    return render_briefing(data)
