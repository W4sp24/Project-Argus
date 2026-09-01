"""Tests for backend/features/calendar/recurrence.py: expanding a stored row.

The unit under test is a pure function over a store row, so these tests build
dicts shaped like ``store.list_events`` returns rather than opening a
database — the coupling that matters is to the *row shape*, not to sqlite.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from backend.features.calendar.recurrence import (
    EXDATE_SEPARATOR,
    MAX_OCCURRENCES,
    OCCURRENCE_ID_SEPARATOR,
    expand,
    split_occurrence_id,
)

# --- fixtures ---------------------------------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    """A ``calendar_events`` row, exactly as ``store.list_events`` returns one."""
    row: dict[str, Any] = {
        "calendar_id": "local",
        "id": "evt-1",
        "title": "Standup",
        "start": "2026-09-01T09:00:00",
        "end": "2026-09-01T09:30:00",
        "all_day": False,
        "location": None,
        "notes": None,
        "rrule": None,
        "exdates": "",
        "updated_at": "2026-08-30T12:00:00+00:00",
    }
    row.update(overrides)
    return row


SEPTEMBER = (date(2026, 9, 1), date(2026, 10, 1))


# --- a row with no rrule ----------------------------------------------------


def test_a_row_without_a_rrule_yields_itself() -> None:
    events = expand(_row(), *SEPTEMBER)

    assert len(events) == 1
    assert events[0].title == "Standup"
    # Passed through verbatim, not re-serialised: the cheap path must not
    # quietly renormalise a string the store already holds.
    assert events[0].start == "2026-09-01T09:00:00"
    assert events[0].end == "2026-09-01T09:30:00"
    assert events[0].all_day is False


def test_a_row_without_a_rrule_outside_the_window_yields_nothing() -> None:
    assert expand(_row(), date(2026, 10, 1), date(2026, 11, 1)) == []


def test_an_event_that_began_before_the_window_and_runs_into_it_is_returned() -> None:
    """Overlap, not start-containment — a conference does not vanish on day two."""
    row = _row(start="2026-08-30T09:00:00", end="2026-09-03T17:00:00")

    assert len(expand(row, date(2026, 9, 1), date(2026, 9, 2))) == 1


def test_an_event_ending_exactly_when_the_window_opens_is_not_in_it() -> None:
    row = _row(start="2026-08-31T08:00:00", end="2026-09-01T00:00:00")

    assert expand(row, *SEPTEMBER) == []


def test_an_event_starting_exactly_when_the_window_closes_is_not_in_it() -> None:
    """Half-open, so adjacent windows never both claim the boundary event."""
    row = _row(start="2026-10-01T00:00:00", end="2026-10-01T01:00:00")

    assert expand(row, *SEPTEMBER) == []


def test_a_zero_length_event_covers_the_instant_it_starts() -> None:
    """``end == start`` is how the timeline path spells "duration unknown"."""
    row = _row(start="2026-09-01T09:00:00", end="2026-09-01T09:00:00")

    assert len(expand(row, date(2026, 9, 1), date(2026, 9, 2))) == 1


def test_an_empty_window_yields_nothing() -> None:
    assert expand(_row(), date(2026, 9, 1), date(2026, 9, 1)) == []


# --- expansion: FREQ, BYDAY, COUNT, UNTIL -----------------------------------


def test_a_weekly_rule_expands_across_a_month() -> None:
    events = expand(_row(rrule="FREQ=WEEKLY"), *SEPTEMBER)

    assert [event.start for event in events] == [
        "2026-09-01T09:00:00",
        "2026-09-08T09:00:00",
        "2026-09-15T09:00:00",
        "2026-09-22T09:00:00",
        "2026-09-29T09:00:00",
    ]


def test_byday_expands_to_each_named_weekday() -> None:
    """2026-09-01 is a Tuesday, so the series starts on the Wednesday after it."""
    events = expand(_row(rrule="FREQ=WEEKLY;BYDAY=MO,WE"), date(2026, 9, 1), date(2026, 9, 15))

    assert [event.start[:10] for event in events] == [
        "2026-09-02",
        "2026-09-07",
        "2026-09-09",
        "2026-09-14",
    ]


def test_count_bounds_the_series() -> None:
    events = expand(_row(rrule="FREQ=DAILY;COUNT=3"), *SEPTEMBER)

    assert [event.start[:10] for event in events] == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_until_bounds_the_series() -> None:
    events = expand(_row(rrule="FREQ=DAILY;UNTIL=20260905T090000"), *SEPTEMBER)

    assert [event.start[:10] for event in events] == [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
        "2026-09-05",
    ]


def test_a_utc_until_bounds_a_series_whose_start_carries_an_offset() -> None:
    """The common real-feed shape: ``DTSTART`` local, ``UNTIL`` in UTC.

    dateutil refuses to build the rule at all when the two disagree about
    awareness, so the UTC stamp has to be moved into the row's own offset
    before it is handed over. 14:00Z is 09:00-05:00, so the fifth occurrence
    is the last one.
    """
    row = _row(
        start="2026-09-01T09:00:00-05:00",
        end="2026-09-01T09:30:00-05:00",
        rrule="FREQ=DAILY;UNTIL=20260905T140000Z",
    )

    events = expand(row, *SEPTEMBER)

    assert len(events) == 5
    assert events[-1].start == "2026-09-05T09:00:00-05:00"


def test_a_utc_until_does_not_sink_a_naive_series() -> None:
    """A naive row plus a UTC ``UNTIL`` must expand, not degrade to one event."""
    events = expand(_row(rrule="FREQ=DAILY;UNTIL=20260905T090000Z"), *SEPTEMBER)

    assert len(events) == 5


# --- bounding: the window and the ceiling -----------------------------------


def test_an_unbounded_daily_rule_is_clipped_to_the_window() -> None:
    """No COUNT, no UNTIL: only the window stops this one."""
    events = expand(_row(rrule="FREQ=DAILY"), date(2026, 9, 1), date(2026, 9, 8))

    assert len(events) == 7
    assert events[0].start[:10] == "2026-09-01"
    assert events[-1].start[:10] == "2026-09-07"


def test_the_same_unbounded_rule_answers_a_later_window() -> None:
    """Expansion is anchored on the row, not on "the first N occurrences"."""
    events = expand(_row(rrule="FREQ=DAILY"), date(2027, 3, 1), date(2027, 3, 4))

    assert [event.start[:10] for event in events] == ["2027-03-01", "2027-03-02", "2027-03-03"]


def test_expansion_never_exceeds_the_ceiling() -> None:
    """A pathological rule is truncated rather than allowed to hang a request.

    Zero-length so the search window is exactly the query window: with a
    duration the search would start before it and the first candidate would
    fall out of the overlap filter, making the count one short of the cap for
    a reason that has nothing to do with the ceiling.
    """
    row = _row(
        start="2026-09-01T00:00:00", end="2026-09-01T00:00:00", rrule="FREQ=MINUTELY"
    )

    events = expand(row, date(2026, 9, 1), date(2026, 9, 2))

    assert len(events) == MAX_OCCURRENCES  # 1440 minutes in the window, capped


# --- duration ---------------------------------------------------------------


def test_duration_is_preserved_on_every_occurrence() -> None:
    row = _row(start="2026-09-01T09:00:00", end="2026-09-01T10:30:00", rrule="FREQ=WEEKLY")

    events = expand(row, *SEPTEMBER)

    assert [event.end for event in events] == [
        "2026-09-01T10:30:00",
        "2026-09-08T10:30:00",
        "2026-09-15T10:30:00",
        "2026-09-22T10:30:00",
        "2026-09-29T10:30:00",
    ]


def test_a_multi_day_occurrence_keeps_its_span() -> None:
    """Duration is ``end - start``, not "same day" — a retreat spans nights."""
    row = _row(start="2026-09-01T09:00:00", end="2026-09-03T17:00:00", rrule="FREQ=WEEKLY")

    events = expand(row, date(2026, 9, 1), date(2026, 9, 15))

    assert [(event.start, event.end) for event in events] == [
        ("2026-09-01T09:00:00", "2026-09-03T17:00:00"),
        ("2026-09-08T09:00:00", "2026-09-10T17:00:00"),
    ]


def test_a_multi_day_occurrence_that_began_before_the_window_is_included() -> None:
    """The window has to be widened by the duration or day two goes missing."""
    row = _row(start="2026-09-01T09:00:00", end="2026-09-03T17:00:00", rrule="FREQ=WEEKLY")

    events = expand(row, date(2026, 9, 2), date(2026, 9, 3))

    assert [event.start for event in events] == ["2026-09-01T09:00:00"]


# --- EXDATE -----------------------------------------------------------------


def test_an_exdate_removes_exactly_one_occurrence() -> None:
    row = _row(rrule="FREQ=WEEKLY", exdates="2026-09-08T09:00:00")

    events = expand(row, *SEPTEMBER)

    assert [event.start[:10] for event in events] == [
        "2026-09-01",
        "2026-09-15",
        "2026-09-22",
        "2026-09-29",
    ]


def test_exdates_are_separated_by_the_module_constant() -> None:
    row = _row(
        rrule="FREQ=WEEKLY",
        exdates=EXDATE_SEPARATOR.join(["2026-09-08T09:00:00", "2026-09-22T09:00:00"]),
    )

    events = expand(row, *SEPTEMBER)

    assert [event.start[:10] for event in events] == ["2026-09-01", "2026-09-15", "2026-09-29"]


def test_an_exdate_in_utc_cancels_an_occurrence_stored_with_an_offset() -> None:
    """An .ics feed writes ``EXDATE`` in UTC; the row's own start is local."""
    row = _row(
        start="2026-09-01T09:00:00-05:00",
        end="2026-09-01T09:30:00-05:00",
        rrule="FREQ=WEEKLY",
        exdates="2026-09-08T14:00:00+00:00",
    )

    events = expand(row, *SEPTEMBER)

    assert "2026-09-08T09:00:00-05:00" not in [event.start for event in events]
    assert len(events) == 4


def test_an_exdate_matching_no_occurrence_changes_nothing() -> None:
    row = _row(rrule="FREQ=WEEKLY", exdates="2026-09-09T09:00:00")

    assert len(expand(row, *SEPTEMBER)) == 5


def test_a_malformed_exdate_entry_is_ignored_without_raising() -> None:
    row = _row(rrule="FREQ=WEEKLY", exdates=f"not-a-date{EXDATE_SEPARATOR}2026-09-08T09:00:00")

    events = expand(row, *SEPTEMBER)

    assert len(events) == 4  # the parseable one still cancelled its occurrence


def test_an_exdate_on_a_row_without_a_rrule_cancels_the_event() -> None:
    """``scope=one`` on a one-off is a cancellation, not a no-op."""
    row = _row(exdates="2026-09-01T09:00:00")

    assert expand(row, *SEPTEMBER) == []


# --- all-day events ---------------------------------------------------------


def test_an_all_day_row_without_a_rrule_stays_date_only() -> None:
    row = _row(start="2026-09-01", end="2026-09-02", all_day=True)

    events = expand(row, *SEPTEMBER)

    assert len(events) == 1
    assert events[0].start == "2026-09-01"
    assert events[0].end == "2026-09-02"
    assert events[0].all_day is True


def test_an_all_day_recurrence_stays_date_only() -> None:
    row = _row(start="2026-09-01", end="2026-09-02", all_day=True, rrule="FREQ=WEEKLY")

    events = expand(row, *SEPTEMBER)

    assert [event.start for event in events] == [
        "2026-09-01",
        "2026-09-08",
        "2026-09-15",
        "2026-09-22",
        "2026-09-29",
    ]
    assert all("T" not in event.start for event in events)
    assert all(event.all_day for event in events)


def test_a_multi_day_all_day_recurrence_keeps_its_span() -> None:
    row = _row(start="2026-09-01", end="2026-09-04", all_day=True, rrule="FREQ=WEEKLY")

    events = expand(row, date(2026, 9, 1), date(2026, 9, 15))

    assert [(event.start, event.end) for event in events] == [
        ("2026-09-01", "2026-09-04"),
        ("2026-09-08", "2026-09-11"),
    ]


def test_an_all_day_exdate_is_a_bare_date() -> None:
    row = _row(start="2026-09-01", end="2026-09-02", all_day=True, rrule="FREQ=WEEKLY")
    row["exdates"] = "2026-09-08"

    events = expand(row, *SEPTEMBER)

    assert "2026-09-08" not in [event.start for event in events]
    assert len(events) == 4


def test_a_timed_row_is_never_silently_turned_into_an_all_day_one() -> None:
    events = expand(_row(rrule="FREQ=WEEKLY"), *SEPTEMBER)

    assert all(event.all_day is False for event in events)
    assert all("T" in event.start for event in events)


# --- timezones --------------------------------------------------------------


def test_the_stored_offset_is_preserved_on_every_occurrence() -> None:
    row = _row(
        start="2026-09-01T09:00:00-05:00",
        end="2026-09-01T09:30:00-05:00",
        rrule="FREQ=WEEKLY",
    )

    events = expand(row, *SEPTEMBER)

    assert [event.start for event in events][:2] == [
        "2026-09-01T09:00:00-05:00",
        "2026-09-08T09:00:00-05:00",
    ]


def test_an_aware_row_and_a_naive_row_both_expand_without_a_type_error() -> None:
    """dateutil raises ``TypeError`` the moment the two are mixed."""
    aware = expand(
        _row(
            start="2026-09-01T09:00:00+02:00",
            end="2026-09-01T10:00:00+02:00",
            rrule="FREQ=DAILY",
        ),
        date(2026, 9, 1),
        date(2026, 9, 4),
    )
    naive = expand(_row(rrule="FREQ=DAILY"), date(2026, 9, 1), date(2026, 9, 4))

    assert len(aware) == 3
    assert len(naive) == 3


def test_a_daily_series_keeps_its_wall_clock_across_a_dst_boundary() -> None:
    """US DST starts on Sunday 2026-03-08; a 09:00 standup stays at 09:00.

    RFC 5545 defines recurrence over local wall time, which is why expanding
    in wall-clock terms is the correct reading and converting to UTC first
    would shift every post-transition occurrence to 08:00. The offset suffix
    stays ``-05:00`` because a row stores an offset, not a zone — Argus cannot
    know that ``-05:00`` meant America/New_York rather than a fixed offset.
    """
    row = _row(
        start="2026-03-06T09:00:00-05:00",
        end="2026-03-06T09:30:00-05:00",
        rrule="FREQ=DAILY",
    )

    events = expand(row, date(2026, 3, 6), date(2026, 3, 12))

    assert [event.start for event in events] == [
        "2026-03-06T09:00:00-05:00",
        "2026-03-07T09:00:00-05:00",
        "2026-03-08T09:00:00-05:00",
        "2026-03-09T09:00:00-05:00",
        "2026-03-10T09:00:00-05:00",
        "2026-03-11T09:00:00-05:00",
    ]


def test_a_utc_row_expands_without_mixing_representations() -> None:
    row = _row(start="2026-09-01T09:00:00Z", end="2026-09-01T09:30:00Z", rrule="FREQ=DAILY;COUNT=2")

    events = expand(row, *SEPTEMBER)

    assert [event.start for event in events] == [
        "2026-09-01T09:00:00+00:00",
        "2026-09-02T09:00:00+00:00",
    ]


# --- occurrence identity ----------------------------------------------------


def test_a_non_recurring_event_keeps_the_row_id() -> None:
    """No series, no occurrence — ``PATCH /events/{id}`` round-trips as-is."""
    events = expand(_row(), *SEPTEMBER)

    assert events[0].id == "evt-1"
    assert split_occurrence_id(events[0].id) == ("evt-1", None)


def test_every_occurrence_gets_a_distinct_composite_id() -> None:
    events = expand(_row(rrule="FREQ=WEEKLY"), *SEPTEMBER)

    ids = [event.id for event in events]
    assert len(set(ids)) == len(ids)
    assert ids[0] == f"evt-1{OCCURRENCE_ID_SEPARATOR}2026-09-01T09:00:00"


def test_an_occurrence_id_round_trips_through_split_occurrence_id() -> None:
    events = expand(_row(rrule="FREQ=WEEKLY"), *SEPTEMBER)

    for event in events:
        # The occurrence half is exactly the event's own start, so the router
        # can append it to `exdates` without reformatting it first.
        assert split_occurrence_id(event.id) == ("evt-1", event.start)


def test_an_all_day_occurrence_id_carries_the_bare_date() -> None:
    row = _row(start="2026-09-01", end="2026-09-02", all_day=True, rrule="FREQ=WEEKLY")

    events = expand(row, *SEPTEMBER)

    assert events[1].id == f"evt-1{OCCURRENCE_ID_SEPARATOR}2026-09-08"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("evt-1", ("evt-1", None)),
        ("evt-1::2026-09-08T09:00:00", ("evt-1", "2026-09-08T09:00:00")),
        ("evt-1::2026-09-08", ("evt-1", "2026-09-08")),
        # A trailing separator carries no occurrence; the row id still comes back.
        ("evt-1::", ("evt-1", None)),
        ("", ("", None)),
    ],
)
def test_split_occurrence_id_cases(value: str, expected: tuple[str, str | None]) -> None:
    assert split_occurrence_id(value) == expected


# --- bad stored data must never raise ---------------------------------------


def test_a_malformed_rrule_degrades_to_the_base_event() -> None:
    """The agenda's hot path: a bad rule shows one event, it does not 500."""
    events = expand(_row(rrule="EVERY OTHER TUESDAY"), *SEPTEMBER)

    assert len(events) == 1
    assert events[0].start == "2026-09-01T09:00:00"
    assert events[0].id == "evt-1"  # the base event, so no occurrence suffix


def test_a_malformed_rrule_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="argus.calendar.recurrence"):
        expand(_row(rrule="EVERY OTHER TUESDAY"), *SEPTEMBER)

    assert "evt-1" in caplog.text


def test_an_unparseable_start_yields_nothing_instead_of_raising() -> None:
    """Nothing can be said about a window it does not fit in, so say nothing."""
    assert expand(_row(start="soon", end="later"), *SEPTEMBER) == []


def test_a_missing_start_yields_nothing_instead_of_raising() -> None:
    assert expand(_row(start=None, end=None), *SEPTEMBER) == []


def test_an_end_before_its_start_does_not_produce_a_negative_duration() -> None:
    row = _row(start="2026-09-01T09:00:00", end="2026-09-01T08:00:00", rrule="FREQ=DAILY;COUNT=2")

    events = expand(row, *SEPTEMBER)

    assert all(event.end >= event.start for event in events)


# --- pass-through -----------------------------------------------------------


def test_title_location_notes_and_calendar_id_ride_along() -> None:
    row = _row(
        title="Lecture",
        location="Room 3",
        notes="bring the reader",
        calendar_id="lectures",
        rrule="FREQ=WEEKLY;COUNT=2",
    )

    events = expand(row, *SEPTEMBER)

    assert len(events) == 2
    assert all(event.title == "Lecture" for event in events)
    assert all(event.location == "Room 3" for event in events)
    assert all(event.notes == "bring the reader" for event in events)
    assert all(event.calendar_id == "lectures" for event in events)


def test_source_and_editable_pass_through_from_the_row() -> None:
    row = _row(rrule="FREQ=WEEKLY;COUNT=2")
    row["source"] = "ics"
    row["editable"] = False

    events = expand(row, *SEPTEMBER)

    assert all(event.source == "ics" for event in events)
    assert all(event.editable is False for event in events)


def test_editable_defaults_to_false_when_the_row_does_not_say() -> None:
    """Fail closed: an unlabelled row must not offer an edit that gets dropped."""
    events = expand(_row(), *SEPTEMBER)

    assert events[0].editable is False


def test_an_editable_row_stays_editable_on_every_occurrence() -> None:
    row = _row(rrule="FREQ=WEEKLY;COUNT=3")
    row["editable"] = True

    events = expand(row, *SEPTEMBER)

    assert len(events) == 3
    assert all(event.editable is True for event in events)
