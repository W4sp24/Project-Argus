"""The source-selection layer that makes deleting the native connectors safe.

The property under test is not "n8n data is preferred" — it is "n8n data is
preferred *only while it is provably fresh*". A push model fails silently, so a
dead workflow must fall back to a working connector rather than keep serving
last week's calendar as if it were today's.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.automations import sources, store

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
def _no_live_connectors(monkeypatch):
    """Connectors answer with a recognisable sentinel so fallback is visible."""
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(gcal, "list_events_safe", lambda day, service=None: (["FROM_GCAL"], None))
    monkeypatch.setattr(todoist, "list_tasks_safe", lambda api=None: (["FROM_TODOIST"], None))


def _push_calendar(conn, *, at: datetime, interval: int | None, text: str = "Standup") -> None:
    store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": text}]},
        expected_interval_seconds=interval,
        now=_clock(at),
    )


def _push_tasks(conn, *, at: datetime, interval: int | None, text: str = "Ship it") -> None:
    store.upsert_widget(
        conn,
        sources.TASKS_SLUG,
        "list",
        {"items": [{"text": text}]},
        expected_interval_seconds=interval,
        now=_clock(at),
    )


# --- fallback when there is no n8n data at all --------------------------------


def test_no_widget_falls_back_to_the_connector(conn) -> None:
    assert sources.calendar_events(conn, DAY, now=_clock(NOW)) == (["FROM_GCAL"], None)
    assert sources.open_tasks(conn, now=_clock(NOW)) == (["FROM_TODOIST"], None)


def test_no_connection_falls_back_to_the_connector() -> None:
    assert sources.calendar_events(None, DAY, now=_clock(NOW))[0] == ["FROM_GCAL"]
    assert sources.open_tasks(None, now=_clock(NOW))[0] == ["FROM_TODOIST"]


# --- fresh n8n data wins ------------------------------------------------------


def test_a_fresh_widget_supersedes_the_connector(conn) -> None:
    _push_calendar(conn, at=NOW - timedelta(minutes=5), interval=900)
    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert error is None
    assert [e.title for e in events] == ["Standup"]
    assert events[0].source == "n8n"


def test_a_fresh_task_widget_supersedes_the_connector(conn) -> None:
    _push_tasks(conn, at=NOW - timedelta(minutes=5), interval=900)
    tasks, error = sources.open_tasks(conn, now=_clock(NOW))
    assert error is None
    assert [t.text for t in tasks] == ["Ship it"]


# --- stale n8n data must NOT mask a working connector -------------------------


def test_a_stale_widget_falls_back_rather_than_serving_old_data(conn) -> None:
    """The property that makes replacing the connectors defensible."""
    # Pushed 2h ago against a 15m cadence — well past 2.5x.
    _push_calendar(conn, at=NOW - timedelta(hours=2), interval=900)
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert events == ["FROM_GCAL"]


def test_a_stale_task_widget_falls_back(conn) -> None:
    _push_tasks(conn, at=NOW - timedelta(hours=2), interval=900)
    tasks, _ = sources.open_tasks(conn, now=_clock(NOW))
    assert tasks == ["FROM_TODOIST"]


def test_a_widget_with_no_declared_cadence_still_expires(conn) -> None:
    """widget_state cannot call an undeclared-cadence widget stale.

    On the dashboard that is fine. Here it would let a workflow that died
    months ago mask a working connector forever, so this layer applies its own
    ceiling.
    """
    _push_calendar(conn, at=NOW - timedelta(hours=1), interval=None)
    fresh, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert [e.title for e in fresh] == ["Standup"]  # inside the ceiling

    long_after = NOW + timedelta(seconds=sources.DEFAULT_FRESHNESS_SECONDS + 60)
    stale, _ = sources.calendar_events(conn, DAY, now=_clock(long_after))
    assert stale == ["FROM_GCAL"]


# --- malformed pushes degrade, never crash ------------------------------------


def test_a_malformed_timeline_payload_falls_back(conn) -> None:
    store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {"entries": "not-a-list"},
        expected_interval_seconds=900,
        now=_clock(NOW),
    )
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert events == ["FROM_GCAL"]


def test_one_bad_entry_does_not_sink_the_rest(conn) -> None:
    store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {
            "entries": [
                "garbage",
                {"at": f"{DAY.isoformat()}T09:00:00", "text": "Good"},
            ]
        },
        expected_interval_seconds=900,
        now=_clock(NOW),
    )
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert [e.title for e in events] == ["Good"]


def test_events_are_filtered_to_the_requested_day(conn) -> None:
    store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {
            "entries": [
                {"at": "2026-08-05T09:00:00", "text": "Today"},
                {"at": "2026-08-06T09:00:00", "text": "Tomorrow"},
            ]
        },
        expected_interval_seconds=900,
        now=_clock(NOW),
    )
    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))
    assert [e.title for e in events] == ["Today"]
