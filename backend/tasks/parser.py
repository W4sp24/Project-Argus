"""Parse Obsidian Tasks syntax out of vault markdown into the task cache.

Supports the Tasks plugin's emoji markers and plain-text bracket fallbacks:

    - [ ] Renew passport 📅 2026-07-20 ⏫ #areas/admin
    - [ ] Read chapter 4 [due: 2026-07-18] [prio: low] #cs201
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from backend.rag.paths import EXCLUDED_TOP_DIRS

CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
DUE_RE = re.compile(r"(?:📅|🗓)\s*(\d{4}-\d{2}-\d{2})|\[due:\s*(\d{4}-\d{2}-\d{2})\]")
SCHEDULED_RE = re.compile(r"⏳\s*(\d{4}-\d{2}-\d{2})|\[scheduled:\s*(\d{4}-\d{2}-\d{2})\]")
DONE_DATE_RE = re.compile(r"✅\s*\d{4}-\d{2}-\d{2}")
CREATED_RE = re.compile(r"➕\s*\d{4}-\d{2}-\d{2}")
PRIORITY_MARKS = [("🔺", "highest"), ("⏫", "high"), ("🔼", "medium"), ("🔽", "low")]
PRIORITY_BRACKET_RE = re.compile(r"\[prio(?:rity)?:\s*(highest|high|medium|low)\]", re.IGNORECASE)
TAG_RE = re.compile(r"#([\w/\-]+)")
BUCKETS = ("overdue", "today", "week", "someday")


class TaskItem(BaseModel):
    """One task, from the vault or a connector."""

    text: str
    done: bool = False
    due: str | None = None
    scheduled: str | None = None
    priority: str | None = None
    tags: list[str] = []
    source: str = "vault"
    path: str | None = None
    line: int | None = None


def parse_task_line(line: str) -> TaskItem | None:
    """Parse one markdown line; None when it isn't a checkbox task."""
    match = CHECKBOX_RE.match(line)
    if match is None:
        return None
    body = match.group(2)

    due = next((a or b for a, b in DUE_RE.findall(body)), None)
    scheduled = next((a or b for a, b in SCHEDULED_RE.findall(body)), None)
    priority = next((name for mark, name in PRIORITY_MARKS if mark in body), None)
    if priority is None:
        bracket = PRIORITY_BRACKET_RE.search(body)
        priority = bracket.group(1).lower() if bracket else None
    tags = TAG_RE.findall(body)

    text = body
    for pattern in (DUE_RE, SCHEDULED_RE, DONE_DATE_RE, CREATED_RE, PRIORITY_BRACKET_RE, TAG_RE):
        text = pattern.sub("", text)
    for mark, _ in PRIORITY_MARKS:
        text = text.replace(mark, "")
    text = re.sub(r"<!--.*?-->", "", text)
    text = " ".join(text.split())

    return TaskItem(
        text=text,
        done=match.group(1).lower() == "x",
        due=due,
        scheduled=scheduled,
        priority=priority,
        tags=tags,
    )


def refresh_cache(conn: sqlite3.Connection, vault_path: Path) -> int:
    """Rescan the vault into tasks_cache; returns the number of open tasks."""
    rows: list[tuple] = []
    for file_path in vault_path.rglob("*.md"):
        relative = file_path.relative_to(vault_path)
        if any(part in EXCLUDED_TOP_DIRS for part in relative.parts):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, raw_line in enumerate(lines, start=1):
            task = parse_task_line(raw_line)
            if task is None:
                continue
            rows.append(
                (
                    relative.as_posix(),
                    number,
                    task.text,
                    int(task.done),
                    task.due,
                    task.scheduled,
                    task.priority,
                    ",".join(task.tags),
                )
            )

    conn.execute("DELETE FROM tasks_cache")
    conn.executemany(
        "INSERT INTO tasks_cache (path, line, text, done, due, scheduled, priority, tags)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return sum(1 for row in rows if not row[3])


def bucket_of(task: TaskItem, today: date) -> str:
    anchor = task.due or task.scheduled
    if anchor is None:
        return "someday"
    try:
        when = date.fromisoformat(anchor)
    except ValueError:
        return "someday"
    if when < today:
        return "overdue"
    if when == today:
        return "today"
    if when <= today + timedelta(days=7):
        return "week"
    return "someday"


def bucketed_tasks(
    conn: sqlite3.Connection, today: date | None = None
) -> dict[str, list[TaskItem]]:
    """Open vault tasks grouped into overdue / today / week / someday."""
    today = today or date.today()
    buckets: dict[str, list[TaskItem]] = {bucket: [] for bucket in BUCKETS}
    for row in conn.execute("SELECT * FROM tasks_cache WHERE done = 0"):
        task = TaskItem(
            text=row["text"],
            done=False,
            due=row["due"],
            scheduled=row["scheduled"],
            priority=row["priority"],
            tags=[tag for tag in row["tags"].split(",") if tag],
            path=row["path"],
            line=row["line"],
        )
        buckets[bucket_of(task, today)].append(task)
    priority_rank = {"highest": 0, "high": 1, "medium": 2, "low": 3, None: 4}
    for bucket in buckets.values():
        bucket.sort(key=lambda task: (task.due or "9999", priority_rank.get(task.priority, 4)))
    return buckets
