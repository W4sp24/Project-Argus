"""Parse Obsidian Tasks syntax out of vault markdown into the task cache.

Supports the Tasks plugin's emoji markers and plain-text bracket fallbacks:

    - [ ] Renew passport 📅 2026-07-20 ⏫ #areas/admin
    - [ ] Read chapter 4 [due: 2026-07-18] [prio: low] #cs201
    - [ ] Water plants 🔁 every week 📅 2026-09-06 #home
"""

from __future__ import annotations

import calendar
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy

CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
DUE_RE = re.compile(r"(?:📅|🗓)\s*(\d{4}-\d{2}-\d{2})|\[due:\s*(\d{4}-\d{2}-\d{2})\]")
SCHEDULED_RE = re.compile(r"⏳\s*(\d{4}-\d{2}-\d{2})|\[scheduled:\s*(\d{4}-\d{2}-\d{2})\]")
DONE_DATE_RE = re.compile(r"✅\s*\d{4}-\d{2}-\d{2}")
CREATED_RE = re.compile(r"➕\s*\d{4}-\d{2}-\d{2}")
PRIORITY_MARKS = [("🔺", "highest"), ("⏫", "high"), ("🔼", "medium"), ("🔽", "low")]
PRIORITY_BRACKET_RE = re.compile(r"\[prio(?:rity)?:\s*(highest|high|medium|low)\]", re.IGNORECASE)
TAG_RE = re.compile(r"#([\w/\-]+)")
# A recurrence rule is free text ("every 2 weeks"), so unlike a date it has no
# shape of its own to match on: it runs from 🔁 to whatever marker comes next,
# which is why the class excludes every other marker plus `#` and the brackets —
# a rule must never swallow the tags or dates written after it. `*` and not `+`
# so a bare 🔁 is still stripped from `text` while parsing as no rule at all.
RECUR_RE = re.compile(r"🔁\s*([^📅🗓⏳✅➕🔺⏫🔼🔽#\[\]]*)|\[repeat:\s*([^\]]+)\]")
BUCKETS = ("overdue", "today", "week", "someday")

#: Weekday names the plugin accepts in `every <weekday>`, as `date.weekday()`.
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
#: The subset of the plugin's rule grammar Argus can roll a date forward by.
#: Deliberately anchored: a rule it half-recognises ("every week on tuesday")
#: must fail the match, not be advanced as if the tail were not there.
RECUR_RULE_RE = re.compile(
    r"^every\s+(?:(\d+)\s+)?(day|week|month|year|" + "|".join(_WEEKDAYS) + r")s?$"
)


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
    #: The upstream record's own id, for tasks that came from a service rather
    #: than the vault. `path`/`line` are the vault's equivalent handle; this is
    #: what lets an n8n action workflow close the real task the user ticked.
    #: `None` for vault tasks, which are edited through the writer instead.
    external_id: str | None = None
    #: Deep link back to the task in its own service, when it supplies one.
    href: str | None = None
    #: The Tasks plugin's 🔁 rule verbatim ("every week"), or None. Additive and
    #: defaulted: this model is serialised over the API and mirrored in
    #: `web/lib/api.ts`, so every existing producer and consumer is unaffected.
    #: The writer, not this parser, is what acts on it — see
    #: :func:`backend.vault.writer.toggle_task_line`.
    recurrence: str | None = None


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
    recurrence = next((a or b for a, b in RECUR_RE.findall(body)), None)
    recurrence = recurrence.strip() if recurrence else None

    text = body
    # RECUR_RE first: its rule ends at the next marker, so stripping the dates
    # or tags before it would let the rule run on into the rest of the line.
    for pattern in (
        RECUR_RE,
        DUE_RE,
        SCHEDULED_RE,
        DONE_DATE_RE,
        CREATED_RE,
        PRIORITY_BRACKET_RE,
        TAG_RE,
    ):
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
        recurrence=recurrence,
    )


def _add_months(anchor: date, months: int) -> date:
    """Shift a date by whole months, clamping to the target month's last day.

    There is no 31 February, and a task the user set to repeat monthly on the
    31st means "every month", not "every month that happens to have 31 days" —
    so the day clamps rather than the month being skipped.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def advance_date(iso: str, rule: str) -> str | None:
    """Move one ISO date forward by a Tasks-plugin recurrence rule.

    Returns None — never raises — for a rule this understands nothing of, or a
    date that is not a real one. The caller (`writer.toggle_task_line`) then
    degrades to a plain toggle: a task the user cannot tick off would be a far
    worse bug than one that quietly stops repeating.
    """
    match = RECUR_RULE_RE.match(" ".join(rule.lower().split()))
    if match is None:
        return None
    try:
        anchor = date.fromisoformat(iso)
    except ValueError:
        return None

    count, unit = match.group(1), match.group(2)
    if unit in _WEEKDAYS:
        # "every 2 mondays" is every *other* Monday, which this does not model;
        # returning None keeps the task tickable rather than repeating it wrong.
        if count is not None:
            return None
        # `or 7`: from a Monday, "every monday" means next week, not zero days.
        ahead = (_WEEKDAYS[unit] - anchor.weekday()) % 7 or 7
        return (anchor + timedelta(days=ahead)).isoformat()

    every = int(count or 1)
    if unit == "day":
        return (anchor + timedelta(days=every)).isoformat()
    if unit == "week":
        return (anchor + timedelta(weeks=every)).isoformat()
    return _add_months(anchor, every if unit == "month" else every * 12).isoformat()


#: A file modified this recently is always re-read, whatever its fingerprint
#: says. Filesystem timestamps are coarse on some volumes, and a note saved
#: twice in the same tick with the same length would otherwise look unchanged.
#: Costs one extra read of one file for a couple of seconds after every edit.
RECENT_EDIT_SECONDS = 2.0


def _task_rows(relative: Path, lines: list[str]) -> list[tuple]:
    """Every task line in one file, as tasks_cache tuples."""
    rows: list[tuple] = []
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
                task.recurrence,
            )
        )
    return rows


def refresh_cache(
    conn: sqlite3.Connection, vault_path: Path, *, taxonomy: Taxonomy | None = None
) -> int:
    """Rescan the vault into tasks_cache; returns the number of open tasks.

    Incremental. This used to read *every* markdown file in the vault and
    rebuild the whole table, and it is called from `/api/agenda`, `/api/tasks`,
    insights, briefing, the external surface and the `list_tasks` chat tool —
    so the cost of opening the dashboard scaled with the size of the vault, and
    was paid again on every poll.

    Now it stats each file and re-reads only the ones whose ``(mtime_ns, size)``
    has moved since ``tasks_cache_files`` last saw them, the same high-water
    mark idiom `backend.telemetry.scan.sync_rows` uses. Files that vanished
    lose their rows; nothing else is touched. The return value is unchanged.
    """
    tax = taxonomy or active_taxonomy()
    known: dict[str, tuple[int, int]] = {
        row["path"]: (row["mtime_ns"], row["size"])
        for row in conn.execute("SELECT path, mtime_ns, size FROM tasks_cache_files")
    }

    recent_floor = time.time() - RECENT_EDIT_SECONDS
    seen: set[str] = set()
    changed: list[tuple[str, tuple]] = []

    for file_path in vault_path.rglob("*.md"):
        relative = file_path.relative_to(vault_path)
        if any(part in tax.excluded_top_dirs for part in relative.parts):
            continue
        key = relative.as_posix()
        try:
            stat = file_path.stat()
        except OSError:
            continue
        seen.add(key)
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        if known.get(key) == fingerprint and stat.st_mtime < recent_floor:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        changed.append((key, fingerprint))
        conn.execute("DELETE FROM tasks_cache WHERE path = ?", (key,))
        conn.executemany(
            "INSERT INTO tasks_cache"
            " (path, line, text, done, due, scheduled, priority, tags, recurrence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _task_rows(relative, lines),
        )

    for key, fingerprint in changed:
        conn.execute(
            "INSERT INTO tasks_cache_files (path, mtime_ns, size) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mtime_ns = excluded.mtime_ns, size = excluded.size",
            (key, *fingerprint),
        )

    for gone in known.keys() - seen:
        conn.execute("DELETE FROM tasks_cache WHERE path = ?", (gone,))
        conn.execute("DELETE FROM tasks_cache_files WHERE path = ?", (gone,))

    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM tasks_cache WHERE done = 0").fetchone()[0]


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
            recurrence=row["recurrence"],
        )
        buckets[bucket_of(task, today)].append(task)
    priority_rank = {"highest": 0, "high": 1, "medium": 2, "low": 3, None: 4}
    for bucket in buckets.values():
        bucket.sort(key=lambda task: (task.due or "9999", priority_rank.get(task.priority, 4)))
    return buckets
