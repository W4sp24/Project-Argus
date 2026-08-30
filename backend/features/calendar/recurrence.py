"""Turning one stored calendar row into the occurrences a window contains.

:func:`expand` is the only thing here that callers use: hand it a row from
:func:`backend.features.calendar.store.list_events` and a half-open
``[window_start, window_end)`` date window, get back
:class:`backend.core.events.CalendarEvent` objects. It sits on the agenda's
hot path — the day rail, ``PLANNER.TIMELINE``, the 07:00 briefing and the
month grid all reach it — which shapes three of the decisions below.

**Three conventions this module defines** (the store deliberately left them
open, so this is where they are pinned down and the router phase can rely on
them):

``exdates`` — the column is a single ``TEXT`` field holding cancelled
occurrence starts joined by :data:`EXDATE_SEPARATOR` (``","``), each one an
ISO-8601 stamp in the same form as the row's own ``start``. Comma rather than
newline or NUL because it survives a JSON round-trip, a URL query string and a
human reading the row in a sqlite browser, and an ISO-8601 stamp can never
contain one. ``DELETE /events/{id}?scope=one`` appends to this; blank and
unreadable entries are skipped rather than treated as errors, because a row
mangled by some future import must not take the agenda down with it.

*Occurrence identity* — every occurrence of a series gets
``f"{row_id}::{start_iso}"`` (:data:`OCCURRENCE_ID_SEPARATOR`), where
``start_iso`` is byte-for-byte the ``start`` on the returned event, so the
router can append the second half straight to ``exdates`` without reformatting
it. A row with no recurrence keeps its plain row id, so the existing
``PATCH``/``DELETE`` by id round-trips unchanged and "composite id" means
exactly "one instance of a series". :func:`split_occurrence_id` recovers the
pair; it splits on the *first* separator, which is unambiguous because event
ids are uuid4 hex and ISO-8601 stamps contain no ``::``.

*Time representation* — expansion happens entirely in **naive wall-clock**
datetimes. This is forced and then load-bearing:

* ``dateutil`` raises ``TypeError`` from ``between()``/``xafter()`` and
  ``ValueError`` from ``rrulestr()`` the moment naive and aware values meet,
  so one representation has to be picked for the rule, the window and the
  exclusion dates alike. The window arrives as bare ``date`` objects, which
  have no offset to adopt.
* RFC 5545 defines recurrence over local wall time: "every weekday at 09:00"
  stays at 09:00 across a daylight-saving transition. Expanding in UTC would
  silently shift every post-transition occurrence by an hour.

The row's own offset (if it has one) is stripped on the way in and re-attached
unchanged to every occurrence on the way out, and any UTC value inside the
rule (``UNTIL=...Z``) or in ``exdates`` is converted into that offset first.
The limit is honest and worth stating: a row stores an *offset*, not a zone,
so Argus cannot know that ``-05:00`` meant America/New_York — an occurrence
after a DST transition keeps the wall clock the user typed and the offset they
typed it with, rather than inventing a transition it has no data for.

All-day rows stay date-only end to end (``2026-09-01``, never
``2026-09-01T00:00:00``): midnight is an implementation detail of the
expansion, not something a consumer should ever see, and the store's
lexicographic ``start`` comparisons depend on the two forms staying distinct.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from dateutil.rrule import rrulestr

from backend.core.events import CalendarEvent

logger = logging.getLogger("argus.calendar.recurrence")

#: How cancelled occurrence starts are joined inside the ``exdates`` column.
#: Comma is RFC 5545's own separator for repeated values inside one EXDATE
#: property, and an ISO-8601 timestamp can never contain one, so the join is
#: unambiguous and the split exact.
#:
#: Defined here rather than in :mod:`ics`, which writes the column this reads:
#: a writer and a reader that each own a copy of the same convention is one
#: edit away from a feed whose cancellations silently stop applying, with
#: nothing failing to say so. ``ics`` imports this name.
EXDATE_SEPARATOR = ","

#: What sits between a row id and an occurrence start in a returned event id.
OCCURRENCE_ID_SEPARATOR = "::"

#: The most occurrences one row may expand to, whatever its rule says.
#:
#: The query window is the *primary* bound — an unbounded ``FREQ=DAILY`` is
#: finite once the search stops at ``window_end``. This ceiling is the backstop
#: for the rule the window cannot tame: ``FREQ=SECONDLY`` over a month is 2.6
#: million occurrences, and building that list would hang a request on the
#: agenda's hot path. Capping the *iteration* (rather than trimming a
#: materialised list afterwards) is what makes that bound real. A month of a
#: sane calendar is a few dozen occurrences, so a thousand is far outside
#: anything a user can produce deliberately and cheap to hold.
MAX_OCCURRENCES = 1000

#: ``source`` for a row that does not carry one. Everything reaching this
#: function came out of ``calendar_events``, so "local" is the truthful label;
#: the ICS sync path overrides it by putting ``source`` on the row.
DEFAULT_SOURCE = "local"

#: ``UNTIL`` written as a UTC stamp — the shape every real .ics feed uses, and
#: the one ``dateutil`` refuses to pair with a naive ``dtstart``.
_UTC_UNTIL_RE = re.compile(r"(UNTIL=)(\d{8}T\d{6})Z", re.IGNORECASE)

_MIDNIGHT = time()


def split_occurrence_id(value: str) -> tuple[str, str | None]:
    """Split an event id into ``(row_id, occurrence_start | None)``.

    ``None`` for the second element means "this is the row itself, not one
    instance of a series" — which is exactly the question
    ``DELETE /events/{id}?scope=one|series`` has to answer before it decides
    between appending an ``exdates`` entry and deleting the row.

    Splits on the *first* separator so the row id is always recovered intact;
    see this module's docstring for why that is unambiguous.
    """
    row_id, separator, occurrence = value.partition(OCCURRENCE_ID_SEPARATOR)
    return row_id, (occurrence if separator and occurrence else None)


def expand(
    event: dict[str, Any], window_start: date, window_end: date
) -> list[CalendarEvent]:
    """The occurrences of one stored row that fall inside ``[start, end)``.

    ``event`` is a ``calendar_events`` row as ``store.list_events`` returns it,
    optionally carrying two extra keys the store has no column for:
    ``source`` and ``editable``. Both pass straight through to every returned
    occurrence. ``editable`` defaults to ``False`` when absent — deliberately
    fail-closed, because an .ics subscription is read-only by protocol and
    offering an edit that would be silently dropped is worse than withholding
    one the caller forgot to enable.

    Overlap, not start-containment, decides membership: an event that began
    before the window and runs into it is returned, or a three-day conference
    would disappear on its second day. (``store.list_events`` filters on
    ``start`` alone, so a caller wanting those has to widen its own query
    window — the store's docstring says as much.)

    Never raises on stored data. A rule this module cannot read degrades to
    the base event and a row whose ``start`` is unreadable degrades to nothing
    at all, both logged — the same contract as
    ``automations.sources._events_from_timeline``, and for the same reason: a
    single bad row must not 500 the agenda.
    """
    row_id = str(event.get("id") or "")
    all_day = bool(event.get("all_day"))

    raw_start = event.get("start")
    parsed_start = _parse(raw_start)
    if parsed_start is None:
        # Nothing true can be said about which window an unreadable stamp
        # belongs to, so say nothing rather than guess a placement.
        logger.warning("calendar event %r has an unreadable start %r; skipping", row_id, raw_start)
        return []

    # One offset for the whole row, taken from `start`: `end` is the same
    # instant's other edge, and a row whose two halves disagree about zone is
    # broken in a way no reading here can repair.
    zone = parsed_start.tzinfo
    start_dt = _wall_clock(parsed_start, zone)
    raw_end = event.get("end")
    end_dt = _wall_clock(_parse(raw_end) or parsed_start, zone)
    # Clamped at zero so an inverted row cannot hand every occurrence an `end`
    # before its `start`. Computed once here and reused, so duration is a
    # property of the series rather than something re-derived per occurrence.
    duration = max(end_dt - start_dt, timedelta())

    window_from = datetime.combine(window_start, _MIDNIGHT)
    window_to = datetime.combine(window_end, _MIDNIGHT)
    excluded = _excluded_starts(event.get("exdates"), zone)

    rrule_text = str(event.get("rrule") or "").strip()
    starts = (
        _rule_starts(rrule_text, row_id, start_dt, zone, duration, window_from, window_to)
        if rrule_text
        else None
    )

    if starts is None:
        # No rule, or one that could not be read: the row is its own single
        # event. `start`/`end` go back out verbatim rather than through a
        # parse-and-reformat, so the overwhelmingly common case stays cheap
        # and cannot renormalise a string the store already holds.
        if start_dt in excluded or not _overlaps(
            start_dt, start_dt + duration, window_from, window_to
        ):
            return []
        end_iso = str(raw_end) if raw_end else str(raw_start)
        return [_as_event(event, str(raw_start), end_iso, row_id, all_day)]

    occurrences: list[CalendarEvent] = []
    for moment in starts:
        if moment in excluded:
            continue
        if not _overlaps(moment, moment + duration, window_from, window_to):
            continue
        start_iso = _format(moment, zone, all_day)
        end_iso = _format(moment + duration, zone, all_day)
        occurrence_id = f"{row_id}{OCCURRENCE_ID_SEPARATOR}{start_iso}"
        occurrences.append(_as_event(event, start_iso, end_iso, occurrence_id, all_day))
    return occurrences


# --- expansion --------------------------------------------------------------


def _rule_starts(
    rrule_text: str,
    row_id: str,
    start_dt: datetime,
    zone: tzinfo | None,
    duration: timedelta,
    window_from: datetime,
    window_to: datetime,
) -> list[datetime] | None:
    """Occurrence starts the window can contain, or ``None`` if the rule is bad.

    ``None`` rather than an exception, and rather than an empty list: a rule
    Argus cannot parse means "we know about one event and nothing more", which
    is a strictly better answer for the user than an empty day.
    """
    # Search from a duration before the window: an occurrence that starts
    # earlier can still run into it, and `xafter` only knows about starts.
    search_from = window_from - duration
    try:
        rule = rrulestr(_utc_until_to_wall_clock(rrule_text, zone), dtstart=start_dt)
        starts: list[datetime] = []
        # `xafter(count=...)` bounds the iteration itself, which `between()`
        # does not: an expansion that is about to be truncated should never
        # have been computed. The break bounds it again by the window, so the
        # work done is min(ceiling, occurrences in window).
        for moment in rule.xafter(search_from, count=MAX_OCCURRENCES, inc=True):
            if moment >= window_to:
                break
            starts.append(moment)
    except Exception:  # noqa: BLE001 - stored data, not a programming error
        logger.warning(
            "calendar event %r has an unreadable rrule %r; falling back to the base event",
            row_id,
            rrule_text,
        )
        return None

    if len(starts) == MAX_OCCURRENCES:
        logger.warning(
            "calendar event %r hit the %d-occurrence ceiling for rrule %r; truncating",
            row_id,
            MAX_OCCURRENCES,
            rrule_text,
        )
    return starts


def _excluded_starts(raw: Any, zone: tzinfo | None) -> set[datetime]:
    """The cancelled occurrence starts in an ``exdates`` column, as wall clock.

    Normalised through the same conversion as the row's ``start``, so an
    ``EXDATE`` an .ics feed wrote in UTC still cancels the occurrence the row
    stores with a local offset — they name the same instant, and comparing
    their raw strings would not notice.
    """
    if not isinstance(raw, str) or not raw.strip():
        return set()

    excluded: set[datetime] = set()
    for entry in raw.split(EXDATE_SEPARATOR):
        moment = _parse(entry.strip())
        if moment is None:
            # debug, not warning: an unreadable exclusion loses a cancellation,
            # which shows up as an event the user already deleted coming back —
            # visible on its own, and not worth a warning per agenda render.
            logger.debug("ignoring unreadable exdate %r", entry)
            continue
        excluded.add(_wall_clock(moment, zone))
    return excluded


# --- the naive/aware boundary -----------------------------------------------


def _parse(value: Any) -> datetime | None:
    """One stored ISO-8601 stamp as a datetime, or ``None`` if it is not one.

    Accepts both of the store's forms: a bare ``YYYY-MM-DD`` (an all-day
    event) parses to midnight, which is what the expansion needs, and
    :func:`_format` puts the date-only form back afterwards.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _wall_clock(moment: datetime, zone: tzinfo | None) -> datetime:
    """``moment`` as a naive wall-clock time in ``zone``.

    An aware value is moved into ``zone`` first, so a UTC stamp and a local
    one that name the same instant collapse to the same naive datetime. With
    no ``zone`` to move into, the offset is simply dropped — the row itself is
    naive, so its wall clock is the only frame available.
    """
    if moment.tzinfo is None:
        return moment
    if zone is not None:
        moment = moment.astimezone(zone)
    return moment.replace(tzinfo=None)


def _format(moment: datetime, zone: tzinfo | None, all_day: bool) -> str:
    """A wall-clock occurrence back in the row's own stored form."""
    if all_day:
        return moment.date().isoformat()
    return moment.replace(tzinfo=zone).isoformat()


def _utc_until_to_wall_clock(rrule_text: str, zone: tzinfo | None) -> str:
    """Rewrite a UTC ``UNTIL`` into the row's wall clock.

    Without this, the single most common real-world rule shape — a local
    ``DTSTART`` with ``UNTIL=...Z``, which is what RFC 5545 actually mandates
    — makes ``rrulestr`` raise ``ValueError`` ("RRULE UNTIL values must be
    specified in UTC when DTSTART is timezone-aware") against the naive
    ``dtstart`` this module expands with, and every such series would degrade
    to a single event.
    """

    def _rewrite(match: re.Match[str]) -> str:
        stamp = datetime.strptime(match.group(2), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        return match.group(1) + _wall_clock(stamp, zone).strftime("%Y%m%dT%H%M%S")

    return _UTC_UNTIL_RE.sub(_rewrite, rrule_text)


# --- windowing --------------------------------------------------------------


def _overlaps(
    start: datetime, end: datetime, window_from: datetime, window_to: datetime
) -> bool:
    """Whether ``[start, end)`` meets ``[window_from, window_to)``.

    Half-open at both ends, matching ``store.list_events``: an event ending
    exactly as the window opens belongs to the previous one, and an event
    starting exactly as it closes belongs to the next, so two adjacent windows
    never both claim the same event.

    A zero-length event is the exception — ``end == start`` is how the n8n
    timeline path spells "the source told us nothing about duration", and
    under the plain rule it would meet no window at all. It is treated as
    covering the instant it starts.
    """
    if start >= window_to:
        return False
    if end <= start:
        return start >= window_from
    return end > window_from


def _as_event(
    row: dict[str, Any], start: str, end: str, event_id: str, all_day: bool
) -> CalendarEvent:
    """One occurrence, with everything descriptive carried over from the row."""
    return CalendarEvent(
        title=str(row.get("title") or "(untitled)"),
        start=start,
        end=end,
        all_day=all_day,
        source=str(row.get("source") or DEFAULT_SOURCE),
        location=row.get("location") or None,
        id=event_id,
        calendar_id=row.get("calendar_id"),
        notes=row.get("notes") or None,
        editable=bool(row.get("editable", False)),
    )
