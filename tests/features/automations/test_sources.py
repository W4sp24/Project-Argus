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
from backend.features.automations.schema import validate_widget_payload

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


def _push_through_validation(conn, slug: str, payload: dict, *, at: datetime) -> None:
    """Store a payload the way a *real* push does — through the validator.

    Every other helper in this file calls `store.upsert_widget` directly, which
    is why the suite was blind to the bug this guards: `validate_widget_payload`
    does not pass payloads through, it **rebuilds each item from a whitelist**.
    A field the whitelist omits is dropped silently at the door, so a mapping
    here that reads it is testing something the HTTP path can never deliver.
    """
    validated = validate_widget_payload(payload)
    store.upsert_widget(
        conn,
        slug,
        validated.kind,
        validated.payload,
        title=validated.title,
        expected_interval_seconds=validated.expected_interval_seconds,
        now=_clock(at),
    )


# --- the push path must actually carry what the consumers read ----------------


def test_a_pushed_task_keeps_the_fields_tasks_due_needs(conn) -> None:
    """The whole n8n task path in one assertion.

    `due` decides whether the agenda shows the task at all, `external_id`
    whether it can ever be completed, and `priority`/`tags`/`href` what the row
    looks like. All five used to be dropped by `_validate_list`, so every
    n8n-sourced task arrived undated and anonymous — and an undated external
    task is filtered out of `/api/agenda` entirely. Installing the Todoist
    template emptied TASKS.DUE.
    """
    _push_through_validation(
        conn,
        sources.TASKS_SLUG,
        {
            "widget": "list",
            "expected_interval_seconds": 900,
            "items": [
                {
                    "text": "Ship it",
                    "id": "7291",
                    "due": "2026-08-05",
                    "priority": "highest",
                    "tags": ["work"],
                    "href": "https://app.todoist.com/app/task/7291",
                }
            ],
        },
        at=NOW,
    )

    tasks, error = sources.open_tasks(conn, now=_clock(NOW))

    assert error is None
    assert len(tasks) == 1
    task = tasks[0]
    assert task.text == "Ship it"
    assert task.due == "2026-08-05"
    assert task.priority == "highest"
    assert task.tags == ["work"]
    assert task.external_id == "7291"
    assert task.href == "https://app.todoist.com/app/task/7291"
    assert task.source == "n8n"


def test_a_pushed_event_keeps_its_end_and_location(conn) -> None:
    """`end` is what gives an event a duration. Dropped, every n8n event was
    zero-length: no duration label, and 0 meeting-hours in insights."""
    _push_through_validation(
        conn,
        sources.CALENDAR_SLUG,
        {
            "widget": "timeline",
            "expected_interval_seconds": 900,
            "entries": [
                {
                    "at": f"{DAY.isoformat()}T09:00:00",
                    "end": f"{DAY.isoformat()}T09:30:00",
                    "text": "Standup",
                    "sub": "Meet",
                    "all_day": False,
                }
            ],
        },
        at=NOW,
    )

    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))

    assert error is None
    assert len(events) == 1
    event = events[0]
    assert event.start == f"{DAY.isoformat()}T09:00:00"
    assert event.end == f"{DAY.isoformat()}T09:30:00"
    assert event.location == "Meet"
    assert event.all_day is False


def test_an_all_day_push_is_marked_all_day(conn) -> None:
    """A bare date with no time component is how both Google and the timeline
    contract spell all-day, so it is inferred when the push omits the flag."""
    _push_through_validation(
        conn,
        sources.CALENDAR_SLUG,
        {
            "widget": "timeline",
            "expected_interval_seconds": 900,
            "entries": [{"at": DAY.isoformat(), "text": "Public holiday"}],
        },
        at=NOW,
    )

    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))

    assert events[0].all_day is True
    assert events[0].end == DAY.isoformat()


def test_a_due_date_is_recovered_from_sub_when_no_due_was_pushed(conn) -> None:
    """Older workflows put the date in the subtext, because that is all the
    contract carried. Recovering an exact ISO date there is the difference
    between a populated panel and an empty one — but only an exact one:
    Todoist's own `due.string` is prose, and guessing at it would stamp
    invented dates on real tasks."""
    _push_through_validation(
        conn,
        sources.TASKS_SLUG,
        {
            "widget": "list",
            "expected_interval_seconds": 900,
            "items": [
                {"text": "Dated", "sub": "2026-08-05"},
                {"text": "Prose", "sub": "every other tuesday"},
            ],
        },
        at=NOW,
    )

    tasks, _ = sources.open_tasks(conn, now=_clock(NOW))

    assert tasks[0].due == "2026-08-05"
    assert tasks[1].due is None


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


# --- B3: a slug spread across instances ----------------------------------


def test_freshest_of_two_instances_wins(conn) -> None:
    """A slug pushed by two different n8n instances: the freshest trustworthy
    push wins, regardless of which instance it came from."""
    store.upsert_widget(
        conn, sources.CALENDAR_SLUG, "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": "Stale instance"}]},
        expected_interval_seconds=900, instance_id="instance-a",
        now=_clock(NOW - timedelta(minutes=30)),
    )
    store.upsert_widget(
        conn, sources.CALENDAR_SLUG, "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": "Fresh instance"}]},
        expected_interval_seconds=900, instance_id="instance-b",
        now=_clock(NOW - timedelta(minutes=1)),
    )

    events, error = sources.calendar_events(conn, DAY, now=_clock(NOW))

    assert error is None
    assert [e.title for e in events] == ["Fresh instance"]


def test_freshest_of_two_instances_falls_back_when_past_the_ceiling(conn) -> None:
    """Both instances pushed with no declared cadence; the freshest of the two
    is still past DEFAULT_FRESHNESS_SECONDS, so neither may mask the
    connector — the ceiling applies per-candidate, not just to the winner."""
    long_ago = NOW - timedelta(seconds=sources.DEFAULT_FRESHNESS_SECONDS + 3600)
    longer_ago = NOW - timedelta(seconds=sources.DEFAULT_FRESHNESS_SECONDS + 7200)
    store.upsert_widget(
        conn, sources.CALENDAR_SLUG, "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": "Dead A"}]},
        expected_interval_seconds=None, instance_id="instance-a", now=_clock(longer_ago),
    )
    store.upsert_widget(
        conn, sources.CALENDAR_SLUG, "timeline",
        {"entries": [{"at": f"{DAY.isoformat()}T09:00:00", "text": "Dead B (freshest)"}]},
        expected_interval_seconds=None, instance_id="instance-b", now=_clock(long_ago),
    )

    events, _ = sources.calendar_events(conn, DAY, now=_clock(NOW))

    assert events == ["FROM_GCAL"]


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


# --- provenance (F7) --------------------------------------------------------


def test_answered_by_reports_the_same_path_the_data_came_from(conn) -> None:
    """The dashboard's VIA N8N marker must agree with whichever path actually
    supplied the data. Deriving it separately in the frontend would be a
    second copy of this decision, free to drift from the real one."""
    assert sources.answered_by(conn, sources.CALENDAR_SLUG, now=_clock(NOW)) == "connector"

    store.upsert_widget(
        conn,
        sources.CALENDAR_SLUG,
        "timeline",
        {"entries": [{"time": "09:00", "text": "Lecture"}]},
        expected_interval_seconds=900,
        now=_clock(NOW),
    )
    assert sources.answered_by(conn, sources.CALENDAR_SLUG, now=_clock(NOW)) == "n8n"


def test_answered_by_says_connector_once_the_widget_goes_stale(conn) -> None:
    """A stale n8n source must never quietly claim credit for data the
    connector actually supplied — the marker follows the same freshness
    ceiling the data path uses."""
    store.upsert_widget(
        conn,
        sources.TASKS_SLUG,
        "list",
        {"items": [{"text": "ship it"}]},
        expected_interval_seconds=900,
        now=_clock(NOW),
    )
    assert sources.answered_by(conn, sources.TASKS_SLUG, now=_clock(NOW)) == "n8n"
    assert (
        sources.answered_by(conn, sources.TASKS_SLUG, now=_clock(NOW + timedelta(hours=6)))
        == "connector"
    )
