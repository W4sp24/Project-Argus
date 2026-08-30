"""Tests for backend/core/events.py and the gcal compat re-export.

``CalendarEvent`` moved out of ``backend.connectors.gcal`` so the agenda shape
stops being a Google artefact. Six call sites import it from the connector by
its old path and were deliberately left unedited, so "the old import still
resolves to the same class" is the property that keeps them honest — not a
second, parallel model that happens to have matching fields.
"""

from backend.connectors.gcal import CalendarEvent as GcalCalendarEvent
from backend.core.events import CalendarEvent


def test_gcal_reexports_the_same_class_object() -> None:
    assert GcalCalendarEvent is CalendarEvent


def test_source_still_defaults_to_gcal() -> None:
    """The connector builds events without naming ``source``; consumers read it."""
    event = CalendarEvent(title="Standup", start="2026-09-01T09:00:00", end="2026-09-01T09:15:00")

    assert event.source == "gcal"
    assert event.all_day is False
    assert event.location is None


def test_new_fields_are_additive_and_default_to_none_or_false() -> None:
    event = CalendarEvent(title="Standup", start="2026-09-01T09:00:00", end="2026-09-01T09:15:00")

    assert event.id is None
    assert event.calendar_id is None
    assert event.notes is None
    assert event.editable is False


def test_a_local_event_can_set_every_new_field() -> None:
    event = CalendarEvent(
        title="Lecture",
        start="2026-09-01T09:00:00",
        end="2026-09-01T10:00:00",
        source="local",
        id="abc",
        calendar_id="local",
        notes="bring the handout",
        rrule="FREQ=WEEKLY",
        editable=True,
    )

    assert event.model_dump() == {
        "title": "Lecture",
        "start": "2026-09-01T09:00:00",
        "end": "2026-09-01T10:00:00",
        "all_day": False,
        "source": "local",
        "location": None,
        "id": "abc",
        "calendar_id": "local",
        "notes": "bring the handout",
        "rrule": "FREQ=WEEKLY",
        "editable": True,
    }
