"""The local calendar merges into the agenda funnel without displacing it.

Two properties, and the second matters more than the first.

1. A locally created event and a subscribed .ics entry reach every consumer
   of `sources.calendar_events` — the agenda, the briefing, insights, the
   planner — with no connector configured and no n8n instance registered.
   That is the feature.

2. **With no local calendar rows, the funnel returns exactly what it returned
   before this feature existed.** Every consumer named above is downstream of
   this one function, so an accidental change here reaches all of them at
   once. Asserting the property is the difference between an orthogonal
   change and one that merely looks orthogonal.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.automations import sources
from backend.features.automations import store as automations_store
from backend.features.calendar import store as calendar_store

DAY = date(2026, 8, 5)
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _clock(at: datetime):
    return lambda: at


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _sentinel_connectors(monkeypatch):
    """Google answers with a sentinel, so "the external half" is visible."""
    from backend.connectors import gcal

    monkeypatch.setattr(
        gcal, "list_events_safe", lambda day, service=None: (["FROM_GCAL"], None)
    )


def _titles(events) -> list[str]:
    """Titles, with the connector sentinel passed through as itself.

    `getattr(event, "title", event)` looks like it would do this and does
    not: `str.title` is a real method, so a sentinel string returns a bound
    method instead of falling back. Hence the explicit isinstance.
    """
    return [event if isinstance(event, str) else event.title for event in events]


def _add_local(conn, title: str, *, start: str, end: str, **kwargs) -> str:
    calendar_store.ensure_default_calendar(conn)
    return calendar_store.upsert_event(
        conn,
        calendar_store.DEFAULT_CALENDAR_ID,
        title=title,
        start=start,
        end=end,
        **kwargs,
    )["id"]


# --- The orthogonality proof -------------------------------------------------


def test_no_local_rows_leaves_the_funnel_byte_for_byte_unchanged(conn) -> None:
    """The guarantee every existing consumer is relying on."""
    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert events == ["FROM_GCAL"]
    assert error is None


def test_an_empty_local_calendar_is_not_a_local_source(conn) -> None:
    """Creating the default calendar without events must change nothing.

    `ensure_default_calendar` runs on first use of the calendar router, so
    this is the state of any install that has opened the page once and
    created nothing. It must not disturb the agenda.
    """
    calendar_store.ensure_default_calendar(conn)
    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert events == ["FROM_GCAL"]
    assert error is None


def test_the_connector_error_still_reaches_the_caller(conn, monkeypatch) -> None:
    """The error slot belongs to the external half and keeps its meaning.

    A local read cannot fail in a way worth reporting, so a failing Google
    connector must still surface its message even while local events render
    — otherwise adding a local calendar would silently hide a broken one.
    """
    from backend.connectors import gcal

    monkeypatch.setattr(gcal, "list_events_safe", lambda day, service=None: ([], "token expired"))
    _add_local(conn, "Gym", start=f"{DAY.isoformat()}T07:00:00", end=f"{DAY.isoformat()}T08:00:00")

    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert error == "token expired"
    assert [event.title for event in events] == ["Gym"]


# --- The feature ------------------------------------------------------------


def test_a_local_event_reaches_the_agenda_with_nothing_connected(conn) -> None:
    _add_local(
        conn,
        "Dentist",
        start=f"{DAY.isoformat()}T14:00:00",
        end=f"{DAY.isoformat()}T15:00:00",
    )
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert "Dentist" in _titles(events)


def test_local_events_lead_the_day_and_do_not_displace_the_external_half(conn) -> None:
    """Merged, not chosen between — and the user's own entry comes first."""
    _add_local(
        conn,
        "Dentist",
        start=f"{DAY.isoformat()}T14:00:00",
        end=f"{DAY.isoformat()}T15:00:00",
    )
    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert error is None
    assert _titles(events) == ["Dentist", "FROM_GCAL"]


def test_a_fresh_n8n_widget_does_not_suppress_local_events(conn) -> None:
    """The precedence chain applies to the external half only.

    Before this, a registered n8n calendar *replaced* the connector. It must
    not replace the user's own calendar too — that would make installing a
    template look like it deleted their events.
    """
    automations_store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": "Standup"}]},
        expected_interval_seconds=3600,
        now=_clock(NOW),
    )
    _add_local(
        conn,
        "Dentist",
        start=f"{DAY.isoformat()}T14:00:00",
        end=f"{DAY.isoformat()}T15:00:00",
    )

    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    titles = _titles(events)
    assert titles == ["Dentist", "Standup"]
    assert "FROM_GCAL" not in titles, "n8n still supersedes the connector"


def test_a_recurring_event_lands_on_a_day_long_after_its_series_started(conn) -> None:
    """The trap `list_events` would have walked into.

    A weekly class is one row whose `start` is the week it began. Filtering
    the window on that column would show it once and never again, which is
    the bug that makes a calendar feel broken rather than empty.
    """
    _add_local(
        conn,
        "Algorithms lecture",
        start="2026-07-01T09:00:00",
        end="2026-07-01T10:30:00",
        rrule="FREQ=WEEKLY;BYDAY=WE",
    )
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert _titles(events) == [
        "Algorithms lecture",
        "FROM_GCAL",
    ], "the 5th of August 2026 is a Wednesday five weeks after the series began"
