"""Tests for backend/features/calendar/store.py: calendars, events, sync state."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.calendar import store as calendar_store
from backend.features.calendar.store import (
    DEFAULT_CALENDAR_ID,
    CalendarNotFound,
    EventNotFound,
    create_calendar,
    delete_calendar,
    delete_event,
    ensure_default_calendar,
    get_calendar,
    get_event,
    list_calendars,
    list_events,
    record_sync,
    replace_events,
    update_calendar,
    upsert_event,
)

# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def _clock(moment: str):
    """A fixed clock — this repo injects one instead of depending on freezegun."""
    return lambda: datetime.fromisoformat(moment).replace(tzinfo=UTC)


# --- the built-in local calendar -------------------------------------------


def test_ensure_default_calendar_creates_the_builtin_local_calendar(conn) -> None:
    calendar = ensure_default_calendar(conn, now=_clock("2026-08-30T12:00:00"))

    assert calendar["id"] == DEFAULT_CALENDAR_ID
    assert calendar["name"] == "Argus"
    assert calendar["kind"] == "local"
    assert calendar["enabled"] is True
    assert calendar["created_at"].startswith("2026-08-30T12:00:00")


def test_ensure_default_calendar_is_idempotent(conn) -> None:
    first = ensure_default_calendar(conn, now=_clock("2026-08-30T12:00:00"))
    second = ensure_default_calendar(conn, now=_clock("2026-09-01T09:00:00"))

    assert second == first  # same row, not a second one and not re-stamped
    assert [row["id"] for row in list_calendars(conn)] == [DEFAULT_CALENDAR_ID]


def test_ensure_default_calendar_keeps_a_renamed_local_calendar(conn) -> None:
    """A user who renamed their local calendar does not get it reset at boot."""
    ensure_default_calendar(conn)
    update_calendar(conn, DEFAULT_CALENDAR_ID, name="Ethan")

    assert ensure_default_calendar(conn)["name"] == "Ethan"


# --- calendars --------------------------------------------------------------


def test_create_calendar_defaults_and_round_trip(conn) -> None:
    calendar = create_calendar(conn, name="Term dates", now=_clock("2026-08-30T12:00:00"))

    assert calendar["kind"] == "local"
    assert calendar["color"] == ""
    assert calendar["url_ref"] is None
    assert calendar["url_display"] is None
    assert calendar["refresh_interval_seconds"] == 3600
    assert calendar["last_sync_at"] is None
    assert calendar["last_sync_error"] is None
    assert calendar["etag"] is None
    assert calendar["enabled"] is True
    assert get_calendar(conn, calendar["id"]) == calendar


def test_create_calendar_mints_a_distinct_id_per_call(conn) -> None:
    first = create_calendar(conn, name="One")
    second = create_calendar(conn, name="Two")

    assert first["id"] != second["id"]


def test_create_calendar_accepts_a_caller_chosen_id(conn) -> None:
    calendar = create_calendar(conn, name="Lectures", calendar_id="lectures")

    assert calendar["id"] == "lectures"


def test_create_calendar_stores_a_subscription_without_the_secret_url(conn) -> None:
    """A secret iCal URL is a credential: only the keyring ref and a redaction."""
    calendar = create_calendar(
        conn,
        name="Google",
        kind="ics",
        url_ref="calendar:google",
        url_display="calendar.google.com/...",
    )

    assert calendar["kind"] == "ics"
    assert calendar["url_ref"] == "calendar:google"
    assert calendar["url_display"] == "calendar.google.com/..."


def test_list_calendars_returns_every_calendar_in_creation_order(conn) -> None:
    ensure_default_calendar(conn, now=_clock("2026-08-30T12:00:00"))
    create_calendar(conn, name="Later", now=_clock("2026-08-31T12:00:00"))

    assert [row["name"] for row in list_calendars(conn)] == ["Argus", "Later"]


def test_update_calendar_changes_only_what_was_passed(conn) -> None:
    calendar = create_calendar(conn, name="Term dates", color="#fff")

    updated = update_calendar(conn, calendar["id"], enabled=False)

    assert updated["enabled"] is False
    assert updated["name"] == "Term dates"
    assert updated["color"] == "#fff"


def test_update_calendar_with_nothing_to_change_is_a_noop(conn) -> None:
    calendar = create_calendar(conn, name="Term dates")

    assert update_calendar(conn, calendar["id"]) == calendar


def test_update_calendar_raises_for_an_unknown_calendar(conn) -> None:
    with pytest.raises(CalendarNotFound):
        update_calendar(conn, "nope", name="x")


def test_get_calendar_returns_none_for_an_unknown_calendar(conn) -> None:
    assert get_calendar(conn, "nope") is None


def test_delete_calendar_also_deletes_its_events(conn) -> None:
    calendar = create_calendar(conn, name="Term dates")
    upsert_event(
        conn, calendar["id"], title="Exam", start="2026-09-01T09:00:00", end="2026-09-01T11:00:00"
    )

    delete_calendar(conn, calendar["id"])

    assert get_calendar(conn, calendar["id"]) is None
    assert list_events(conn, "2026-01-01T00:00:00", "2027-01-01T00:00:00") == []


def test_delete_calendar_leaves_other_calendars_events_alone(conn) -> None:
    keep = create_calendar(conn, name="Keep")
    drop = create_calendar(conn, name="Drop")
    upsert_event(
        conn, keep["id"], title="Kept", start="2026-09-01T09:00:00", end="2026-09-01T10:00:00"
    )
    upsert_event(
        conn, drop["id"], title="Gone", start="2026-09-01T11:00:00", end="2026-09-01T12:00:00"
    )

    delete_calendar(conn, drop["id"])

    titles = [
        event["title"] for event in list_events(conn, "2026-01-01T00:00:00", "2027-01-01T00:00:00")
    ]
    assert titles == ["Kept"]


def test_delete_calendar_raises_for_an_unknown_calendar(conn) -> None:
    with pytest.raises(CalendarNotFound):
        delete_calendar(conn, "nope")


# --- events -----------------------------------------------------------------


def test_upsert_event_round_trips_every_field(conn) -> None:
    ensure_default_calendar(conn)

    event = upsert_event(
        conn,
        DEFAULT_CALENDAR_ID,
        title="Lecture",
        start="2026-09-01T09:00:00",
        end="2026-09-01T10:00:00",
        location="Room 3",
        notes="bring the handout",
        rrule="FREQ=WEEKLY;COUNT=4",
        exdates="2026-09-08T09:00:00",
        now=_clock("2026-08-30T12:00:00"),
    )

    assert event["calendar_id"] == DEFAULT_CALENDAR_ID
    assert event["title"] == "Lecture"
    assert event["all_day"] is False
    assert event["location"] == "Room 3"
    assert event["notes"] == "bring the handout"
    assert event["rrule"] == "FREQ=WEEKLY;COUNT=4"
    assert event["exdates"] == "2026-09-08T09:00:00"
    assert event["updated_at"].startswith("2026-08-30T12:00:00")
    assert get_event(conn, DEFAULT_CALENDAR_ID, event["id"]) == event


def test_upsert_event_mints_an_id_when_the_caller_has_none(conn, monkeypatch) -> None:
    ensure_default_calendar(conn)
    monkeypatch.setattr(calendar_store, "_new_id", lambda: "fixed-id")

    event = upsert_event(
        conn,
        DEFAULT_CALENDAR_ID,
        title="Lecture",
        start="2026-09-01T09:00:00",
        end="2026-09-01T10:00:00",
    )

    assert event["id"] == "fixed-id"


def test_upsert_event_updates_in_place_on_a_second_call(conn) -> None:
    ensure_default_calendar(conn)
    created = upsert_event(
        conn,
        DEFAULT_CALENDAR_ID,
        title="Lecture",
        start="2026-09-01T09:00:00",
        end="2026-09-01T10:00:00",
        now=_clock("2026-08-30T12:00:00"),
    )

    updated = upsert_event(
        conn,
        DEFAULT_CALENDAR_ID,
        event_id=created["id"],
        title="Lecture (moved)",
        start="2026-09-01T14:00:00",
        end="2026-09-01T15:00:00",
        all_day=True,
        now=_clock("2026-08-31T08:00:00"),
    )

    assert updated["id"] == created["id"]
    assert updated["title"] == "Lecture (moved)"
    assert updated["all_day"] is True
    assert updated["updated_at"].startswith("2026-08-31T08:00:00")
    assert len(list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")) == 1


def test_upsert_event_raises_for_an_unknown_calendar(conn) -> None:
    with pytest.raises(CalendarNotFound):
        upsert_event(
            conn, "nope", title="X", start="2026-09-01T09:00:00", end="2026-09-01T10:00:00"
        )


def test_get_event_returns_none_for_an_unknown_event(conn) -> None:
    ensure_default_calendar(conn)

    assert get_event(conn, DEFAULT_CALENDAR_ID, "nope") is None


def test_delete_event_removes_it(conn) -> None:
    ensure_default_calendar(conn)
    event = upsert_event(
        conn,
        DEFAULT_CALENDAR_ID,
        title="Lecture",
        start="2026-09-01T09:00:00",
        end="2026-09-01T10:00:00",
    )

    delete_event(conn, DEFAULT_CALENDAR_ID, event["id"])

    assert get_event(conn, DEFAULT_CALENDAR_ID, event["id"]) is None


def test_delete_event_raises_for_an_unknown_event(conn) -> None:
    ensure_default_calendar(conn)

    with pytest.raises(EventNotFound):
        delete_event(conn, DEFAULT_CALENDAR_ID, "nope")


# --- list_events: the half-open window --------------------------------------


def _at(conn, calendar_id: str, start: str) -> str:
    return upsert_event(conn, calendar_id, title=start, start=start, end=start)["id"]


def test_list_events_includes_an_event_starting_exactly_at_start(conn) -> None:
    ensure_default_calendar(conn)
    _at(conn, DEFAULT_CALENDAR_ID, "2026-09-01T00:00:00")

    events = list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")

    assert [event["start"] for event in events] == ["2026-09-01T00:00:00"]


def test_list_events_excludes_an_event_starting_exactly_at_end(conn) -> None:
    """Half-open, so back-to-back windows never render the same event twice."""
    ensure_default_calendar(conn)
    _at(conn, DEFAULT_CALENDAR_ID, "2026-09-02T00:00:00")

    assert list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00") == []
    assert len(list_events(conn, "2026-09-02T00:00:00", "2026-09-03T00:00:00")) == 1


def test_list_events_is_ordered_by_start(conn) -> None:
    ensure_default_calendar(conn)
    for start in ("2026-09-01T15:00:00", "2026-09-01T09:00:00", "2026-09-01T12:00:00"):
        _at(conn, DEFAULT_CALENDAR_ID, start)

    events = list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")

    assert [event["start"] for event in events] == [
        "2026-09-01T09:00:00",
        "2026-09-01T12:00:00",
        "2026-09-01T15:00:00",
    ]


def test_list_events_can_be_scoped_to_some_calendars(conn) -> None:
    mine = create_calendar(conn, name="Mine")
    theirs = create_calendar(conn, name="Theirs")
    _at(conn, mine["id"], "2026-09-01T09:00:00")
    _at(conn, theirs["id"], "2026-09-01T10:00:00")

    events = list_events(
        conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00", calendar_ids=[mine["id"]]
    )

    assert [event["calendar_id"] for event in events] == [mine["id"]]


def test_list_events_with_an_empty_calendar_filter_returns_nothing(conn) -> None:
    """``[]`` means "no calendars selected", which is not the same as ``None``."""
    ensure_default_calendar(conn)
    _at(conn, DEFAULT_CALENDAR_ID, "2026-09-01T09:00:00")

    assert list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00", calendar_ids=[]) == []


# --- replace_events: the ICS sync primitive ---------------------------------


def _feed_event(title: str, start: str) -> dict:
    return {"id": title, "title": title, "start": start, "end": start}


def test_replace_events_swaps_the_calendars_whole_contents(conn) -> None:
    calendar = create_calendar(conn, name="Subscribed", kind="ics")
    replace_events(conn, calendar["id"], [_feed_event("old", "2026-09-01T09:00:00")])

    replace_events(
        conn,
        calendar["id"],
        [_feed_event("new", "2026-09-01T10:00:00"), _feed_event("also", "2026-09-01T11:00:00")],
    )

    titles = [
        event["title"] for event in list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")
    ]
    assert titles == ["new", "also"]


def test_replace_events_with_an_empty_feed_clears_the_calendar(conn) -> None:
    calendar = create_calendar(conn, name="Subscribed", kind="ics")
    replace_events(conn, calendar["id"], [_feed_event("old", "2026-09-01T09:00:00")])

    replace_events(conn, calendar["id"], [])

    assert list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00") == []


def test_replace_events_touches_only_its_own_calendar(conn) -> None:
    local = ensure_default_calendar(conn)
    subscribed = create_calendar(conn, name="Subscribed", kind="ics")
    upsert_event(
        conn, local["id"], title="Mine", start="2026-09-01T09:00:00", end="2026-09-01T10:00:00"
    )
    replace_events(conn, subscribed["id"], [_feed_event("theirs", "2026-09-01T11:00:00")])

    replace_events(conn, subscribed["id"], [])

    titles = [
        event["title"] for event in list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")
    ]
    assert titles == ["Mine"]


def test_replace_events_rolls_back_entirely_when_one_row_is_bad(conn) -> None:
    """One transaction: a feed that fails half way leaves the old rows intact.

    The alternative — delete-all, then fail on insert #7 — would empty a
    subscribed calendar because a remote feed served one malformed entry.
    """
    calendar = create_calendar(conn, name="Subscribed", kind="ics")
    replace_events(conn, calendar["id"], [_feed_event("old", "2026-09-01T09:00:00")])

    with pytest.raises(Exception):  # noqa: B017 - sqlite3 chooses the exact type
        replace_events(
            conn,
            calendar["id"],
            [
                _feed_event("fine", "2026-09-01T10:00:00"),
                {"id": "bad", "title": None, "start": "2026-09-01T11:00:00", "end": ""},
            ],
        )

    titles = [
        event["title"] for event in list_events(conn, "2026-09-01T00:00:00", "2026-09-02T00:00:00")
    ]
    assert titles == ["old"]


def test_replace_events_raises_for_an_unknown_calendar(conn) -> None:
    with pytest.raises(CalendarNotFound):
        replace_events(conn, "nope", [])


# --- record_sync ------------------------------------------------------------


def test_record_sync_stores_the_etag_and_timestamp(conn) -> None:
    calendar = create_calendar(conn, name="Subscribed", kind="ics")

    synced = record_sync(
        conn, calendar["id"], etag='W/"abc"', error=None, now=_clock("2026-08-30T12:00:00")
    )

    assert synced["etag"] == 'W/"abc"'
    assert synced["last_sync_error"] is None
    assert synced["last_sync_at"].startswith("2026-08-30T12:00:00")


def test_record_sync_records_a_failure(conn) -> None:
    calendar = create_calendar(conn, name="Subscribed", kind="ics")

    synced = record_sync(conn, calendar["id"], etag=None, error="404 Not Found")

    assert synced["last_sync_error"] == "404 Not Found"


def test_record_sync_clears_a_previous_error_on_success(conn) -> None:
    """A recovered subscription must stop showing yesterday's failure."""
    calendar = create_calendar(conn, name="Subscribed", kind="ics")
    record_sync(conn, calendar["id"], etag=None, error="404 Not Found")

    synced = record_sync(conn, calendar["id"], etag='W/"abc"', error=None)

    assert synced["last_sync_error"] is None
    assert synced["etag"] == 'W/"abc"'


def test_record_sync_raises_for_an_unknown_calendar(conn) -> None:
    with pytest.raises(CalendarNotFound):
        record_sync(conn, "nope", etag=None, error=None)
