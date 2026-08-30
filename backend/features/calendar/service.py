"""Reading the local calendar: stored rows in, concrete occurrences out.

`store` answers in rows and `recurrence` turns one row into occurrences;
this is the seam that puts them together, so no caller has to know that a
weekly class is one row rather than fifty. Everything that reads the local
calendar — the agenda funnel, the `/api/calendar` router, the briefing —
comes through here.

The hot-path rule: **nothing in this module raises.** Its callers are the
dashboard, the 07:00 briefing and the insights page, none of which has
anywhere useful to put an exception. A bad stored row loses that row and
logs; it does not empty the panel or 500 the request. That is the same
bargain `automations.sources` already strikes, for the same reason.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from backend.core.events import CalendarEvent
from backend.features.calendar import recurrence, store

logger = logging.getLogger("argus.calendar.service")


def events_in_window(
    conn: sqlite3.Connection,
    window_start: date,
    window_end: date,
    *,
    calendar_ids: list[str] | None = None,
) -> list[CalendarEvent]:
    """Every local occurrence in the half-open window ``[start, end)``.

    Sorted by start, with an all-day event ahead of a timed one on the same
    day: a date-only string is a prefix of every datetime on that date, so
    plain string ordering already does this and no key function has to encode
    the rule twice.
    """
    try:
        rows = store.candidates_for_window(
            conn,
            window_start.isoformat(),
            window_end.isoformat(),
            calendar_ids=calendar_ids,
        )
    except sqlite3.Error:
        # A missing table is the shape this takes on a database that predates
        # the feature and has not been through init_schema yet.
        logger.exception("local calendar unreadable; serving no local events")
        return []

    # Which calendar an event belongs to is what decides whether Argus may
    # write it back, and the row does not carry that — only the calendar does.
    # Stamped here rather than inside `expand`, which is given one row and has
    # no way to know. Without it every listed event defaults to
    # `editable=False` and the whole calendar renders read-only: the create
    # dialog says the event is yours, the grid says it is not.
    writable = {
        row["id"]: row.get("kind") != "ics" for row in _calendars_by_id(conn).values()
    }

    events: list[CalendarEvent] = []
    for row in rows:
        for event in recurrence.expand(row, window_start, window_end):
            event.editable = writable.get(row["calendar_id"], False)
            event.rrule = row.get("rrule") or None
            events.append(event)
    events.sort(key=lambda event: (event.start, event.title))
    return events


def _calendars_by_id(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        return {row["id"]: row for row in store.list_calendars(conn)}
    except sqlite3.Error:
        logger.exception("calendar list unreadable; treating every event as read-only")
        return {}


def events_on(
    conn: sqlite3.Connection, day: date, *, calendar_ids: list[str] | None = None
) -> list[CalendarEvent]:
    """The local occurrences on one day.

    A one-day window over :func:`events_in_window` rather than its own query:
    the day rail and the month grid must never disagree about whether an
    event lands on a date, and the surest way to guarantee that is for them
    to be the same code path.
    """
    return events_in_window(conn, day, day + timedelta(days=1), calendar_ids=calendar_ids)
