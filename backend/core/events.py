"""The neutral calendar-event model.

``CalendarEvent`` was defined inside :mod:`backend.connectors.gcal`, which
made Argus's agenda shape a Google artefact: the briefing, the tasks router
and the automations source funnel all imported their event type from a
connector most installs never configure. It lives here instead, in
``backend.core`` — below every producer of an event, so a locally created
one, a subscribed .ics entry and a Google event all speak the same shape and
none of them owns it.

``gcal.py`` re-exports the name, so the existing
``from backend.connectors.gcal import CalendarEvent`` call sites keep working
unedited (see ``tests/core/test_events.py``, which pins that they resolve to
this very class rather than a look-alike).
"""

from __future__ import annotations

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    """One calendar event in Argus's agenda shape."""

    title: str
    start: str
    end: str
    all_day: bool = False
    #: Which producer this came from. Still defaults to ``"gcal"`` even now
    #: that Google is one source among several: the connector constructs these
    #: without naming the field, and every existing consumer (and its tests)
    #: reads that default. The local store and the .ics sync set it
    #: explicitly.
    source: str = "gcal"
    #: Where the event is, when the source says. Additive and defaulted, so the
    #: connector path (which does not read it) and every existing consumer are
    #: unaffected; the n8n timeline path fills it from the entry's `sub`.
    location: str | None = None
    #: The stored event's own id, and the calendar it belongs to — the pair
    #: that identifies a row in ``calendar_events``. ``None`` for every
    #: producer that has no store behind it (Google, an n8n timeline widget),
    #: which is why they are defaulted rather than required.
    id: str | None = None
    calendar_id: str | None = None
    notes: str | None = None
    #: The recurrence rule this occurrence came from (RFC 5545, e.g.
    #: ``FREQ=WEEKLY;BYDAY=MO``), or ``None`` for a one-off. Present so a
    #: client can *show* that an event repeats and hand the rule back
    #: unchanged when editing something else about it. Without it an edit
    #: has to either drop the rule or guess at it, and dropping it silently
    #: converts a weekly class into a single event.
    #:
    #: ``None`` for every producer that expands server-side (Google) or has
    #: no rule at all (an n8n timeline entry).
    rrule: str | None = None
    #: Whether Argus may write this event back. ``False`` by default, because
    #: every pre-existing producer is read-only: Google is read-only here,
    #: an .ics subscription is read-only by protocol, and an n8n widget's
    #: timeline is someone else's data. The UI renders a non-editable event as
    #: a read-only chip rather than offering an edit that would be silently
    #: dropped.
    editable: bool = False
