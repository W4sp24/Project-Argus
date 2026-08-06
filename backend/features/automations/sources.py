"""Where calendar events and external tasks come from.

Every consumer of calendar/task data — the agenda, the planner, the 07:00
briefing, insights, ``TASKS.DUE``, ``PLANNER.TIMELINE`` — calls through here
instead of importing :mod:`backend.connectors` directly.

**This indirection is the entire reason the native connectors can be deleted
rather than rewritten.** Both paths can feed these functions at once: the n8n
path takes precedence when a source is registered and its data is fresh, and
the native connector supplies the data otherwise. No consumer learns which one
answered, so removing the connectors later is a deletion inside this module and
nothing else has to change.

The return shape is deliberately the connectors' own ``(data, error | None)``
contract rather than something new, so the rewiring is a one-line import change
at each call site and existing error handling keeps working unaltered.

Freshness is the safety property. A push model fails *silently* — nobody calls
anything, and a panel just shows yesterday's data or nothing at all. So a
widget only supersedes a connector while it is inside its expected cadence;
past that it is treated as absent and the connector answers again if it can. A
stale n8n source must never quietly replace a working connector.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from backend.features.automations import store

logger = logging.getLogger("argus.automations.sources")

#: Widget slugs the shipped templates push to. These are the contract between
#: `templates/google-calendar.json` / `templates/todoist.json` and this module.
CALENDAR_SLUG = "calendar"
TASKS_SLUG = "tasks"

#: When a widget declares no cadence we cannot prove it is fresh, so it is
#: only trusted for this long. Half a day is generous enough for a daily
#: workflow and short enough that a dead one stops masking a live connector.
DEFAULT_FRESHNESS_SECONDS = 12 * 60 * 60


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_trustworthy(row: dict[str, Any], *, now: Callable[[], datetime]) -> bool:
    """Whether ``row`` is currently fit to serve as this consumer's source of truth.

    ``False`` for a widget that has never been pushed to, that has gone
    stale per its declared cadence, or (for a widget that declares no
    cadence at all) has gone past :data:`DEFAULT_FRESHNESS_SECONDS` — see
    the module docstring for why that ceiling exists.
    """
    if not row.get("last_seen_at"):
        return False

    if store.widget_state(row, now=now) == "stale":
        return False

    # widget_state cannot mark a widget with no declared cadence as stale,
    # because nothing was declared to compare against. Outside the dashboard
    # that is fine; here it would let a workflow that died months ago keep
    # masking a working connector forever, so apply a ceiling of our own.
    if row.get("expected_interval_seconds") is None:
        age = (now() - datetime.fromisoformat(row["last_seen_at"])).total_seconds()
        if age > DEFAULT_FRESHNESS_SECONDS:
            return False

    return True


def _fresh_widget(
    conn: sqlite3.Connection, slug: str, *, now: Callable[[], datetime] = _utcnow
) -> dict[str, Any] | None:
    """The freshest currently-trustworthy widget at ``slug``, across every instance.

    B1's ``(instance_id, slug)`` key means the same slug can now carry a
    separate row per registered n8n instance — this consumer does not care
    which instance answered, only which one has the most recent good data, so
    every candidate is filtered through :func:`_is_trustworthy` first (a
    stale or long-dead instance's row must never win just for being present)
    and the freshest survivor by ``last_seen_at`` wins. Returns ``None`` when
    no instance's row is currently trustworthy, so the caller falls back
    rather than serving old data as if it were current.
    """
    candidates = [w for w in store.list_widgets(conn, include_hidden=True) if w["slug"] == slug]
    trustworthy = [w for w in candidates if _is_trustworthy(w, now=now)]
    if not trustworthy:
        return None
    return max(trustworthy, key=lambda w: datetime.fromisoformat(w["last_seen_at"]))


def calendar_events(
    conn: sqlite3.Connection | None,
    day: date,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> tuple[list[Any], str | None]:
    """Events for ``day`` as ``(events, error)``.

    Prefers a fresh ``calendar`` timeline widget pushed by an n8n workflow;
    falls back to the native Google Calendar connector.
    """
    if conn is not None:
        widget = _fresh_widget(conn, CALENDAR_SLUG, now=now)
        if widget is not None:
            events = _events_from_timeline(widget, day)
            if events is not None:
                return events, None

    from backend.connectors import gcal

    return gcal.list_events_safe(day)


def open_tasks(
    conn: sqlite3.Connection | None, *, now: Callable[[], datetime] = _utcnow
) -> tuple[list[Any], str | None]:
    """External (non-vault) tasks as ``(tasks, error)``.

    Prefers a fresh ``tasks`` list widget pushed by an n8n workflow; falls back
    to the native Todoist connector.
    """
    if conn is not None:
        widget = _fresh_widget(conn, TASKS_SLUG, now=now)
        if widget is not None:
            tasks = _tasks_from_list(widget)
            if tasks is not None:
                return tasks, None

    from backend.connectors import todoist

    return todoist.list_tasks_safe()


def _events_from_timeline(widget: dict[str, Any], day: date) -> list[Any] | None:
    """Map a ``timeline`` widget payload onto CalendarEvent objects.

    Returns ``None`` — meaning "fall back" — rather than raising if the payload
    is not shaped the way this consumer needs. A malformed push must degrade to
    the connector, never to an exception on the dashboard's hot path.
    """
    from backend.connectors.gcal import CalendarEvent

    entries = widget.get("payload", {}).get("entries")
    if not isinstance(entries, list):
        return None

    events: list[CalendarEvent] = []
    wanted = day.isoformat()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        at = str(entry.get("at") or "")
        if not at.startswith(wanted):
            continue
        try:
            events.append(
                CalendarEvent(
                    title=str(entry.get("text") or "(untitled)"),
                    start=at,
                    end=str(entry.get("end") or at),
                    all_day=bool(entry.get("all_day", False)),
                    source="n8n",
                )
            )
        except Exception:  # noqa: BLE001 - a bad entry must not sink the rest
            logger.debug("skipping malformed timeline entry: %r", entry)
    return events


def _tasks_from_list(widget: dict[str, Any]) -> list[Any] | None:
    """Map a ``list`` widget payload onto TaskItem objects. ``None`` to fall back."""
    from backend.vault.tasks import TaskItem

    items = widget.get("payload", {}).get("items")
    if not isinstance(items, list):
        return None

    tasks: list[TaskItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            tasks.append(
                TaskItem(
                    text=text,
                    done=bool(item.get("done", False)),
                    due=item.get("due"),
                    priority=item.get("priority"),
                    tags=list(item.get("tags") or []),
                    source="n8n",
                )
            )
        except Exception:  # noqa: BLE001 - one bad row must not sink the list
            logger.debug("skipping malformed list item: %r", item)
    return tasks
