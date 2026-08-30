"""The local calendar's data layer.

Plain ``sqlite3``, plain-dict CRUD over ``calendars`` and ``calendar_events``,
matching :mod:`backend.features.automations.store` and
:mod:`backend.features.quick_links.store`: no ORM, no pydantic in here (the
wire model is :class:`backend.core.events.CalendarEvent`, and keeping it out
of the store is what lets the sync path move rows around without paying
validation for every entry of a thousand-event feed). Every function that
reads the clock takes ``now`` as a keyword-only parameter defaulting to
:func:`_utcnow`, so tests inject a fixed clock — there is no ``freezegun``/
``time-machine`` dependency in this repo.

Two kinds of calendar share one table. A ``kind='local'`` calendar is written
by the user through Argus; a ``kind='ics'`` one mirrors a subscribed URL and
is replaced wholesale on every sync (:func:`replace_events`). The subscription
URL itself is a credential — anyone holding a secret iCal link can read the
calendar — so it lives in the OS keyring under ``url_ref`` (invariant I4) and
never in this database; only the redacted ``url_display`` is stored here.

Timestamps are ISO-8601 strings throughout, and event ``start``/``end`` are
compared as strings. That is deliberate rather than lazy: ISO-8601 sorts
lexicographically in the same order it sorts chronologically, so a plain
``start >= ? AND start < ?`` uses ``idx_calendar_events_start`` directly, and
the same column can hold an all-day ``2026-09-01`` date beside a timed
instant without a second representation.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

#: The built-in calendar every install has without configuring anything. Its
#: id is a fixed string rather than a minted uuid so the boot path, the
#: writer's ``schedule`` fallback and the frontend can all name it without
#: first querying for it.
DEFAULT_CALENDAR_ID = "local"
DEFAULT_CALENDAR_NAME = "Argus"

#: Kinds this feature writes. Deliberately *not* a CHECK constraint on the
#: column — see the schema comment in ``backend.core.db.SCHEMA`` for why.
CALENDAR_KINDS = ("local", "ics")


class CalendarError(Exception):
    """Base for every calendar-domain failure raised by this module."""


class CalendarNotFound(CalendarError):  # noqa: N818 - reads as a 404 at the call site
    """Raised when a calendar id does not name a row in ``calendars``."""


class EventNotFound(CalendarError):  # noqa: N818 - reads as a 404 at the call site
    """Raised when an event id does not name a row in the given calendar."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    """ISO-8601 UTC string for a (possibly naive) datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _new_id() -> str:
    """A fresh event/calendar id.

    Its own function purely so a test can monkeypatch one deterministic id
    instead of threading a factory parameter through every write path.
    """
    return uuid.uuid4().hex


def _calendar_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "color": row["color"],
        "url_ref": row["url_ref"],
        "url_display": row["url_display"],
        "refresh_interval_seconds": row["refresh_interval_seconds"],
        "last_sync_at": row["last_sync_at"],
        "last_sync_error": row["last_sync_error"],
        "etag": row["etag"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "calendar_id": row["calendar_id"],
        "id": row["id"],
        "title": row["title"],
        "start": row["start"],
        "end": row["end"],
        "all_day": bool(row["all_day"]),
        "location": row["location"],
        "notes": row["notes"],
        "rrule": row["rrule"],
        # Opaque here on purpose: the separator convention belongs to the
        # recurrence expansion that reads it, not to storage.
        "exdates": row["exdates"],
        "updated_at": row["updated_at"],
    }


def _require_calendar(conn: sqlite3.Connection, calendar_id: str) -> None:
    """Raise :class:`CalendarNotFound` unless ``calendar_id`` exists.

    Every write that names a calendar checks first. ``calendar_events`` has no
    foreign key to ``calendars`` — see the schema comment — so without this a
    typo'd id would insert orphan rows that no calendar lists and no delete
    ever reaches.
    """
    row = conn.execute("SELECT 1 FROM calendars WHERE id = ?", (calendar_id,)).fetchone()
    if row is None:
        raise CalendarNotFound(f"calendar not found: {calendar_id}")


# --- calendars --------------------------------------------------------------


def create_calendar(
    conn: sqlite3.Connection,
    *,
    name: str,
    calendar_id: str | None = None,
    kind: str = "local",
    color: str = "",
    url_ref: str | None = None,
    url_display: str | None = None,
    refresh_interval_seconds: int = 3600,
    enabled: bool = True,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Create one calendar and return it.

    ``calendar_id`` is minted when the caller has none; the built-in local
    calendar passes its fixed :data:`DEFAULT_CALENDAR_ID`, and a future
    import path can preserve an id it was given.

    ``url_ref`` is a *keyring reference* (``calendar:{id}``), never the URL —
    see this module's docstring.
    """
    row_id = calendar_id or _new_id()
    conn.execute(
        "INSERT INTO calendars "
        "(id, name, kind, color, url_ref, url_display, refresh_interval_seconds, "
        "enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            name,
            kind,
            color,
            url_ref,
            url_display,
            refresh_interval_seconds,
            1 if enabled else 0,
            _isoformat(now()),
        ),
    )
    conn.commit()
    calendar = get_calendar(conn, row_id)
    assert calendar is not None  # just written above
    return calendar


def get_calendar(conn: sqlite3.Connection, calendar_id: str) -> dict[str, Any] | None:
    """One calendar by id, or ``None`` if it doesn't exist."""
    row = conn.execute("SELECT * FROM calendars WHERE id = ?", (calendar_id,)).fetchone()
    return None if row is None else _calendar_row(row)


def list_calendars(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every calendar, oldest first (so the built-in local one leads the list).

    ``id`` is a tiebreaker, not part of the sort the caller cares about: two
    calendars created in the same second must still order deterministically.
    """
    rows = conn.execute("SELECT * FROM calendars ORDER BY created_at, id").fetchall()
    return [_calendar_row(row) for row in rows]


def update_calendar(
    conn: sqlite3.Connection,
    calendar_id: str,
    *,
    name: str | None = None,
    color: str | None = None,
    url_ref: str | None = None,
    url_display: str | None = None,
    refresh_interval_seconds: int | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Update only the fields that were passed; omitted ones are left as-is.

    ``None`` therefore means "don't touch", not "set to NULL" — the same
    partial-update shape as ``automations.store.set_widget_flags``. Nothing
    here needs to *clear* a column: a subscription that loses its URL is
    deleted rather than emptied, and :func:`record_sync` owns the two columns
    that legitimately go back to NULL.
    """
    fields: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("name", name),
        ("color", color),
        ("url_ref", url_ref),
        ("url_display", url_display),
        ("refresh_interval_seconds", refresh_interval_seconds),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if enabled else 0)

    _require_calendar(conn, calendar_id)
    if fields:
        values.append(calendar_id)
        conn.execute(f"UPDATE calendars SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()

    calendar = get_calendar(conn, calendar_id)
    assert calendar is not None  # existence checked above
    return calendar


def delete_calendar(conn: sqlite3.Connection, calendar_id: str) -> None:
    """Delete a calendar and every event on it, in one transaction.

    The cascade is written out here rather than declared as a foreign key:
    ``calendar_events`` deliberately has no FK to ``calendars`` (a sync writes
    thousands of rows and should not pay a parent lookup each time, and
    ``PRAGMA foreign_keys`` is a per-connection setting, so a connection that
    forgot it would silently drop the guarantee). Leaving the events behind
    would be worse than either: they belong to no calendar, so nothing lists
    them and nothing ever deletes them.
    """
    _require_calendar(conn, calendar_id)
    with conn:
        conn.execute("DELETE FROM calendar_events WHERE calendar_id = ?", (calendar_id,))
        conn.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))


def ensure_default_calendar(
    conn: sqlite3.Connection, *, now: Callable[[], datetime] = _utcnow
) -> dict[str, Any]:
    """Create the built-in local calendar if it isn't there, and return it.

    This is what makes the feature zero-setup: a fresh install can create an
    event before it has ever opened a settings page. Idempotent by
    ``INSERT ... ON CONFLICT DO NOTHING``, so it is safe to call on every
    connection — and a user who renamed or recoloured their local calendar
    keeps those edits, because the conflict path writes nothing at all.
    """
    conn.execute(
        "INSERT INTO calendars (id, name, kind, color, refresh_interval_seconds, created_at) "
        "VALUES (?, ?, 'local', '', 3600, ?) ON CONFLICT(id) DO NOTHING",
        (DEFAULT_CALENDAR_ID, DEFAULT_CALENDAR_NAME, _isoformat(now())),
    )
    conn.commit()
    calendar = get_calendar(conn, DEFAULT_CALENDAR_ID)
    assert calendar is not None  # just written above
    return calendar


# --- events -----------------------------------------------------------------

_EVENT_COLUMNS = (
    "calendar_id, id, title, start, end, all_day, location, notes, rrule, exdates, updated_at"
)


def upsert_event(
    conn: sqlite3.Connection,
    calendar_id: str,
    *,
    event_id: str | None = None,
    title: str,
    start: str,
    end: str,
    all_day: bool = False,
    location: str | None = None,
    notes: str | None = None,
    rrule: str | None = None,
    exdates: str = "",
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Insert or replace one event on ``calendar_id`` and return it.

    Always a full overwrite (no COALESCE-and-retain like
    ``automations.store.upsert_widget``): both callers hand over a complete
    event — the editor sends the whole form, and a feed entry is the whole
    truth about that entry — so there is nothing stale worth preserving.
    """
    _require_calendar(conn, calendar_id)
    row_id = event_id or _new_id()
    conn.execute(
        f"INSERT INTO calendar_events ({_EVENT_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(calendar_id, id) DO UPDATE SET title = excluded.title, "
        "start = excluded.start, end = excluded.end, all_day = excluded.all_day, "
        "location = excluded.location, notes = excluded.notes, rrule = excluded.rrule, "
        "exdates = excluded.exdates, updated_at = excluded.updated_at",
        (
            calendar_id,
            row_id,
            title,
            start,
            end,
            1 if all_day else 0,
            location,
            notes,
            rrule,
            exdates,
            _isoformat(now()),
        ),
    )
    conn.commit()
    event = get_event(conn, calendar_id, row_id)
    assert event is not None  # just written above
    return event


def get_event(
    conn: sqlite3.Connection, calendar_id: str, event_id: str
) -> dict[str, Any] | None:
    """One event by ``(calendar_id, id)``, or ``None`` if it doesn't exist."""
    row = conn.execute(
        "SELECT * FROM calendar_events WHERE calendar_id = ? AND id = ?",
        (calendar_id, event_id),
    ).fetchone()
    return None if row is None else _event_row(row)


def list_events(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    *,
    calendar_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Events whose ``start`` falls in the half-open window ``[start, end)``.

    Half-open so two adjacent windows (this week, next week) never both claim
    the event that begins exactly on the boundary — the duplicate-row bug
    every closed-interval agenda query eventually grows.

    The filter is on ``start`` alone, which means a long event that began
    before the window is *not* returned. That is the right primitive for the
    agenda and day-rail callers, and the recurrence expansion a later phase
    adds needs the same window semantics; a caller wanting overlaps should
    widen its own window rather than make this predicate lie.

    ``calendar_ids`` unset (``None``) means every calendar. An explicit empty
    sequence means "none selected" and returns ``[]`` — the distinction
    matters, because a UI with every calendar unticked must show nothing
    rather than everything.
    """
    query = f"SELECT {_EVENT_COLUMNS} FROM calendar_events WHERE start >= ? AND start < ?"
    params: list[Any] = [start, end]
    if calendar_ids is not None:
        if not calendar_ids:
            return []
        placeholders = ", ".join("?" for _ in calendar_ids)
        query += f" AND calendar_id IN ({placeholders})"
        params.extend(calendar_ids)
    # id is a tiebreaker only, so two events starting in the same minute keep
    # a stable order between calls instead of shuffling under the user.
    query += " ORDER BY start, id"
    rows = conn.execute(query, params).fetchall()
    return [_event_row(row) for row in rows]


def find_event(conn: sqlite3.Connection, event_id: str) -> dict[str, Any] | None:
    """One event by id alone, whichever calendar holds it.

    The API addresses an event by its id and nothing else — that is all the
    UI has when the user clicks a row — so the calendar has to be looked up
    rather than supplied. :func:`get_event` stays the exact ``(calendar_id,
    id)`` accessor for callers that already know both.

    Ids are unique in practice: local ones are ``uuid4().hex``, and a feed's
    are its own UIDs. Two feeds *could* in principle publish the same UID, so
    the ordering is pinned rather than left to SQLite's discretion, making the
    answer stable between calls instead of shuffling under a user who clicked
    the same row twice.
    """
    row = conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM calendar_events WHERE id = ? ORDER BY calendar_id LIMIT 1",
        (event_id,),
    ).fetchone()
    return None if row is None else _event_row(row)


def candidates_for_window(
    conn: sqlite3.Connection,
    window_start: str,
    window_end: str,
    *,
    calendar_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Rows that *might* place an occurrence in ``[window_start, window_end)``.

    Deliberately a superset, because the exact answer needs the RRULE engine
    and this is SQL. :func:`recurrence.expand` narrows it.

    Two kinds of row that :func:`list_events` correctly omits have to be here:

    * **A recurring row.** Its stored ``start`` is when the *series* began —
      a class that started in September is one row dated September, and
      filtering on ``start`` would hide it from every week after the first.
      Every rule-bearing row is therefore a candidate whatever its date; the
      window is applied during expansion, where it can be applied truthfully.
    * **A long event that began earlier.** A three-day conference starting
      Friday is still happening on Sunday.

    ISO-8601 sorts lexicographically, which is why these string comparisons
    are date comparisons. A date-only ``YYYY-MM-DD`` is a prefix of any
    datetime on that day, so it orders before every time on it — exactly the
    boundary behaviour an all-day event wants.
    """
    query = (
        f"SELECT {_EVENT_COLUMNS} FROM calendar_events"
        " WHERE ((rrule IS NOT NULL AND rrule <> '') OR (start < ? AND end >= ?))"
    )
    params: list[Any] = [window_end, window_start]
    if calendar_ids is not None:
        if not calendar_ids:
            return []
        placeholders = ", ".join("?" for _ in calendar_ids)
        query += f" AND calendar_id IN ({placeholders})"
        params.extend(calendar_ids)
    query += " ORDER BY start, id"
    return [_event_row(row) for row in conn.execute(query, params).fetchall()]


def delete_event(conn: sqlite3.Connection, calendar_id: str, event_id: str) -> None:
    """Delete one event, or raise :class:`EventNotFound`."""
    cursor = conn.execute(
        "DELETE FROM calendar_events WHERE calendar_id = ? AND id = ?", (calendar_id, event_id)
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise EventNotFound(f"event not found: {calendar_id}/{event_id}")


def replace_events(
    conn: sqlite3.Connection,
    calendar_id: str,
    events: Iterable[dict[str, Any]],
    *,
    now: Callable[[], datetime] = _utcnow,
) -> int:
    """Replace every event on ``calendar_id`` with ``events``. Returns how many.

    The ICS sync primitive. A subscribed feed is the whole truth about its
    calendar — an entry that vanished from it was cancelled — so diffing
    would only be a slower way to reach the same rows, and would have to
    invent an identity for entries whose UID changed.

    Delete-then-insert runs inside **one** transaction (``with conn``, which
    rolls back on any exception). Without that, a feed that fails to insert
    its seventh entry would leave the calendar empty rather than unchanged:
    one malformed remote entry would silently erase a calendar the user can
    no longer see, and the next sync would happily "confirm" it.

    Rows are written with the current time as ``updated_at`` rather than a
    per-entry timestamp from the feed: it records when Argus last saw the
    entry, which is the question the sync path actually asks.
    """
    _require_calendar(conn, calendar_id)
    ts = _isoformat(now())
    rows = [
        (
            calendar_id,
            event.get("id") or _new_id(),
            event.get("title"),
            event.get("start"),
            event.get("end"),
            1 if event.get("all_day") else 0,
            event.get("location"),
            event.get("notes"),
            event.get("rrule"),
            event.get("exdates") or "",
            ts,
        )
        for event in events
    ]
    with conn:
        conn.execute("DELETE FROM calendar_events WHERE calendar_id = ?", (calendar_id,))
        conn.executemany(
            f"INSERT INTO calendar_events ({_EVENT_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def record_sync(
    conn: sqlite3.Connection,
    calendar_id: str,
    *,
    etag: str | None,
    error: str | None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Record the outcome of one sync attempt and return the calendar.

    ``etag`` and ``error`` are both written every time, unconditionally: a
    successful sync passes ``error=None`` and that is what *clears* the
    previous failure. Leaving a stale ``last_sync_error`` on a recovered
    subscription would keep an error badge on a calendar that is fine, which
    is the kind of lie that teaches users to ignore the badge.

    ``last_sync_at`` is when the attempt happened, success or not, so a
    subscription that has been failing for a week still says when it was last
    tried.
    """
    _require_calendar(conn, calendar_id)
    conn.execute(
        "UPDATE calendars SET last_sync_at = ?, last_sync_error = ?, etag = ? WHERE id = ?",
        (_isoformat(now()), error, etag, calendar_id),
    )
    conn.commit()
    calendar = get_calendar(conn, calendar_id)
    assert calendar is not None  # existence checked above
    return calendar
