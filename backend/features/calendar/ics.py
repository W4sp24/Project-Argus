"""Subscribing to a published .ics feed: fetch it, parse it, nothing else.

This is the module that makes the feature reachable. Connecting Google
Calendar the supported way means a Cloud Console project, a Desktop OAuth
client, a downloaded JSON secret, a browser consent screen and
``pip install .[gcal]`` — almost nobody finishes that. Google, Outlook, Apple
and every university timetable *also* publish a secret iCal URL, which is one
copy-paste. So this path is the one most installs will actually use, and its
failure modes have to be legible: a rotated secret URL must say "that address
no longer exists", never "your calendar is empty".

Scope is deliberately narrow. :func:`fetch` does HTTP and caching;
:func:`parse` turns text into events. Neither touches sqlite, the keyring or
the clock — ``sync.py`` owns the transaction and the ``last_sync_error``, and
``recurrence.py`` owns expanding an RRULE. This module therefore carries
recurrence through verbatim rather than expanding it: expanding here would
turn a five-week series into five rows that no later edit could re-collapse.

HTTP conventions (injectable client, one error class, error text written for
the human who has to fix it) mirror
:mod:`backend.features.automations.n8n_client`; the client is synchronous
because the only caller is the scheduler's sync job, which is not async.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from icalendar import Calendar, Event, vRecur

from backend.core.events import CalendarEvent
from backend.features.calendar import recurrence

logger = logging.getLogger(__name__)

#: A feed sync runs unattended on an hourly job, so it can afford to wait for
#: a slow university timetable server — but not forever, because a hung fetch
#: holds the scheduler thread. Shorter than n8n's 120s (nothing here is
#: creating a workflow) and longer than its 20s probe (nothing here is being
#: watched by a user in a dialog).
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Refuse to buffer more than this from a feed. The URL is user-supplied and
#: the *backend* fetches it, so "however much the server sends" is not an
#: acceptable memory budget: a hostile or broken endpoint could otherwise
#: stream until the process dies. 10 MiB is roughly two orders of magnitude
#: above a busy personal calendar's yearly export (a dense 5000-event feed is
#: ~1.5 MB) and still small enough to hold twice over while decoding.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

#: After ``webcal://`` normalisation, these are the only schemes we will
#: dereference. ``file://`` would make a pasted URL a directory-traversal read
#: of the machine Argus runs on.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: How multiple EXDATE values are joined into the single ``TEXT`` column
#: ``calendar_events.exdates``. Imported, not redefined: this module writes
#: that column and :mod:`recurrence` reads it, so a second copy of the
#: separator here would be one edit away from a feed whose cancellations
#: silently stop applying, with nothing failing to say so.
EXDATE_SEPARATOR = recurrence.EXDATE_SEPARATOR

#: Matches ``connectors/gcal.py``, so an untitled event reads the same however
#: it arrived.
UNTITLED = "(untitled)"


class IcsError(RuntimeError):
    """Every failure this module raises, fetch or parse.

    One class rather than a hierarchy: the only caller (``sync.py``) writes
    whatever it catches into ``calendars.last_sync_error`` and shows it to the
    user verbatim, so the message carries the distinction that matters and a
    subclass would have nothing to switch on.
    """


class IcsEvent(CalendarEvent):
    """A feed entry: a :class:`CalendarEvent` plus its unexpanded recurrence.

    ``rrule``/``exdates`` are not on :class:`~backend.core.events.CalendarEvent`
    because they are meaningless to the producers that have no store behind
    them (Google's connector expands server-side; an n8n timeline widget has
    no rule at all). Subclassing keeps :func:`parse`'s return type a genuine
    ``list[CalendarEvent]`` for every consumer typed against the shared model,
    while ``sync.py`` gets the two extra columns it must persist without a
    parallel structure to keep in step.

    Both fields mirror ``calendar_events`` exactly — ``rrule TEXT`` nullable,
    ``exdates TEXT NOT NULL DEFAULT ''`` — so ``store.replace_events`` can take
    ``model_dump()`` straight from here.
    """

    rrule: str | None = None
    exdates: str = ""


@dataclass(frozen=True)
class ParseResult:
    """What one feed yielded, and how much of it was unusable.

    ``skipped`` exists so the UI can say "3 events skipped" rather than
    silently under-reporting. Real feeds contain junk, and a subscription that
    quietly drops a fifth of its entries looks identical to one that is
    working.
    """

    events: list[IcsEvent]
    skipped: int


# --- fetch ------------------------------------------------------------------


def _normalise_url(url: str) -> str:
    """Canonicalise a pasted subscription URL, or raise :class:`IcsError`.

    ``webcal://`` is the single most likely input: it is what Apple's
    "Subscribe" links and Google's "secret address" buttons hand out, and
    httpx cannot fetch that scheme at all. It is a plain alias for HTTPS over
    the same host and path, so rewriting the scheme is the whole fix — but
    getting it wrong makes the flagship path fail on the most probable paste.
    """
    parts = urlsplit(url.strip())
    # urlsplit lowercases the scheme, so WEBCAL:// arrives here already folded.
    if parts.scheme in ("webcal", "webcals"):
        parts = parts._replace(scheme="https")
    if parts.scheme not in ALLOWED_SCHEMES:
        got = parts.scheme or "no scheme"
        raise IcsError(
            f"that is not a calendar URL Argus will fetch ({got}) — a subscription "
            "must be an http://, https:// or webcal:// address."
        )
    return urlunsplit(parts)


def _status_message(status: int, url: str) -> str:
    """Prose for a non-2xx, written for the person who has to fix it."""
    host = urlsplit(url).netloc or url
    if status in (401, 403):
        return (
            f"{host} refused the request (HTTP {status}) — this feed is not public, "
            "or its secret address has been revoked. Re-copy the private iCal URL "
            "from the calendar's own settings."
        )
    if status == 404:
        # Said explicitly, because the tempting shorthand ("no events found")
        # is the one reading that sends the user nowhere: a secret iCal URL is
        # regenerated whenever it is reset, and the stale one 404s forever.
        return (
            f"there is no calendar at that address (HTTP 404 from {host}) — a secret "
            "iCal URL changes whenever it is reset, so copy the current one from the "
            "calendar's settings. This is not a calendar with no events in it."
        )
    if status >= 500:
        return (
            f"{host} is failing (HTTP {status}) — that is the feed's server, not the "
            "URL. It is worth retrying later."
        )
    return f"{host} returned HTTP {status} for that calendar URL."


def _too_large(seen_bytes: int | None = None) -> IcsError:
    limit_mb = MAX_RESPONSE_BYTES // (1024 * 1024)
    seen = f" (over {seen_bytes} bytes)" if seen_bytes else ""
    return IcsError(
        f"that calendar feed is too large{seen} — Argus reads at most {limit_mb} MB "
        "from a subscription."
    )


def fetch(
    url: str, *, etag: str | None = None, client: httpx.Client | None = None
) -> tuple[str | None, str | None]:
    """Download one .ics feed. Returns ``(body, etag)``.

    ``(None, etag)`` means **304 Not Modified**: the server says nothing has
    changed, and the caller must keep the events it already has rather than
    treat an absent body as an emptied calendar. Pass the returned ``etag``
    back on the next call to get that answer — a hourly job against an
    unchanged feed then costs one conditional request instead of a megabyte.

    ``client`` is injectable so tests drive the whole path through
    ``httpx.MockTransport``; when it is given, closing it stays the caller's
    job.
    """
    target = _normalise_url(url)
    headers = {
        # Some publishers content-negotiate the same URL between an .ics body
        # and an HTML "here is your calendar" page.
        "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.8",
    }
    if etag:
        headers["If-None-Match"] = etag

    owned = client is None
    http = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        # Streamed rather than read whole, so the size cap can abort mid-download
        # instead of after a hostile server has already been handed the memory.
        # `follow_redirects` is set per-request as well as on the owned client:
        # feed URLs redirect constantly (webcal aliases, CDN fronts, short
        # links), and an injected client must not have to remember to opt in.
        with http.stream("GET", target, headers=headers, follow_redirects=True) as response:
            if response.status_code == 304:
                # RFC 7232 allows a 304 to carry a fresh validator; falling back
                # to the one we sent keeps the cache addressable either way.
                return None, response.headers.get("ETag") or etag
            if response.status_code >= 400:
                raise IcsError(_status_message(response.status_code, target))

            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise _too_large(int(declared))

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                # Checked per chunk, not just against Content-Length: a chunked
                # response declares no length, and a hostile one may lie.
                if total > MAX_RESPONSE_BYTES:
                    raise _too_large()
                chunks.append(chunk)

            # RFC 5545 mandates UTF-8, but exporters ship BOMs (icalendar
            # refuses to parse past one) and the occasional stray byte. Decoding
            # with `replace` loses one character where refusing would lose the
            # whole calendar.
            body = b"".join(chunks).decode("utf-8-sig", errors="replace")
            return body, response.headers.get("ETag")
    except httpx.HTTPError as exc:
        host = urlsplit(target).netloc or target
        raise IcsError(f"could not reach {host}: {exc}") from exc
    finally:
        if owned:
            http.close()


# --- parse ------------------------------------------------------------------


def _is_all_day(value: date | datetime) -> bool:
    """A bare ``date`` (not a ``datetime``) is iCalendar's all-day marker.

    ``datetime`` subclasses ``date``, so the isinstance order matters.
    """
    return isinstance(value, date) and not isinstance(value, datetime)


def _format(value: date | datetime, all_day: bool) -> str:
    """One instant as the ISO-8601 string ``CalendarEvent`` stores.

    All-day values become ``YYYY-MM-DD``; everything else becomes an
    offset-aware datetime **converted to UTC**. That conversion is not
    cosmetic: ``store.list_events`` compares ``start`` as a *string*, which
    only sorts chronologically while every row shares one offset — a feed in
    ``TZID=Europe/London`` stored verbatim would sort itself into the wrong
    day of the agenda.

    A naive ("floating") value is read as UTC, matching ``store._isoformat``.
    Floating times are rare in published feeds and there is no better answer
    available here: the vault has no configured timezone this module can see.
    """
    if all_day:
        return (value.date() if isinstance(value, datetime) else value).isoformat()
    moment = value if isinstance(value, datetime) else datetime.combine(value, time.min)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def _first(prop):
    """The first value of a property a broken feed may have repeated."""
    return prop[0] if isinstance(prop, list) else prop


def _text(component, name: str) -> str | None:
    """One text property as ``str``, or ``None`` when absent or blank."""
    value = component.get(name)
    if value is None:
        return None
    text = str(_first(value)).strip()
    return text or None


def _end_of(component, start_value: date | datetime, all_day: bool) -> date | datetime:
    """When the event finishes, from DTEND, DURATION, or neither.

    RFC 5545 makes DTEND optional: an event may carry DURATION instead, or
    nothing at all. All three shapes appear in real feeds, and treating a
    missing DTEND as a parse failure would drop exactly the entries (a
    reminder, a deadline) that a user most wants to see.

    With neither, a timed event is zero-length and an all-day one lasts a
    single day — which, DTEND being exclusive, means the *next* date.
    """
    dtend = component.get("DTEND")
    if dtend is not None:
        return _first(dtend).dt
    duration = component.get("DURATION")
    if duration is not None:
        return start_value + _first(duration).dt
    return start_value + timedelta(days=1) if all_day else start_value


def _exdates_of(component, all_day: bool) -> str:
    """Every EXDATE, joined with :data:`EXDATE_SEPARATOR`.

    Publishers split these two different ways — Google emits one EXDATE line
    per cancelled instance, Outlook packs them comma-separated into one — and
    icalendar reflects that difference in its return type (a ``vDDDLists``, or
    a list of them). Both shapes flatten to the same string here so
    ``recurrence.py`` never has to know which publisher it came from.
    """
    raw = component.get("EXDATE")
    if raw is None:
        return ""
    properties = raw if isinstance(raw, list) else [raw]
    return EXDATE_SEPARATOR.join(
        _format(value.dt, all_day) for prop in properties for value in prop.dts
    )


def _identity(component, seen: set[str]) -> str | None:
    """A feed-unique id for this entry, or ``None`` when it has no UID.

    UID alone is not unique within a feed. A modified instance of a series
    repeats its series' UID and distinguishes itself by RECURRENCE-ID, and
    that shape is common in Google exports. ``store.replace_events`` INSERTs
    without an upsert against a ``PRIMARY KEY (calendar_id, id)``, so two rows
    sharing an id abort the whole sync transaction and the user's calendar
    stops updating — a duplicate here is not a cosmetic problem.

    ``None`` is left for the store to mint an id for; a UID-less entry has no
    identity worth preserving across syncs anyway.
    """
    uid = _text(component, "UID")
    if uid is None:
        return None
    recurrence_id = component.get("RECURRENCE-ID")
    if recurrence_id is not None:
        instance = _first(recurrence_id).dt
        uid = f"{uid}#{_format(instance, _is_all_day(instance))}"
    candidate, suffix = uid, 2
    while candidate in seen:
        candidate = f"{uid}#{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _event_from(component, *, calendar_id: str, source: str, seen: set[str]) -> IcsEvent:
    """One VEVENT as an :class:`IcsEvent`. Raises on anything unusable."""
    dtstart = component.get("DTSTART")
    if dtstart is None:
        # icalendar 6 drops a property whose value it cannot parse rather than
        # raising, so a corrupt DTSTART arrives as a missing one. Without a
        # start there is no event: nothing could place it on a day.
        raise IcsError("VEVENT has no usable DTSTART")
    start_value = _first(dtstart).dt
    all_day = _is_all_day(start_value)
    rrule = component.get("RRULE")

    return IcsEvent(
        title=_text(component, "SUMMARY") or UNTITLED,
        start=_format(start_value, all_day),
        # DTEND is carried through **exclusive** for an all-day event, exactly
        # as iCalendar (and Google's API, and therefore `connectors/gcal.py`)
        # expresses it: a one-day event on the 3rd is 2026-09-03 → 2026-09-04.
        # Converting to an inclusive last day here would make .ics events the
        # one producer with different end semantics, and every consumer would
        # have to know which kind it was holding.
        end=_format(_end_of(component, start_value, all_day), all_day),
        all_day=all_day,
        source=source,
        location=_text(component, "LOCATION"),
        id=_identity(component, seen),
        calendar_id=calendar_id,
        notes=_text(component, "DESCRIPTION"),
        # An .ics subscription is read-only by protocol — there is no way to
        # push a change back up the URL it came from.
        editable=False,
        # Carried, never expanded: `recurrence.py` expands at read time,
        # bounded by the query window.
        rrule=_first(rrule).to_ical().decode("utf-8", "replace") if rrule is not None else None,
        exdates=_exdates_of(component, all_day),
    )


def parse_feed(text: str, *, calendar_id: str, source: str = "ics") -> ParseResult:
    """Parse a whole .ics document, reporting entries it had to skip.

    A single malformed VEVENT must not lose the feed — real calendars contain
    junk, and a subscription that fails wholesale because one entry has a
    corrupt DTSTART is a subscription the user turns off. A failure of the
    *document* is different and does raise: reporting an unparseable body as
    "0 events" would render a broken feed as an empty calendar, which is the
    exact lie this module exists to avoid.
    """
    try:
        # icalendar refuses to parse past a BOM, and exporters emit them; fetch
        # already strips one, but `parse` is also called on cached text.
        calendar = Calendar.from_ical(text.lstrip("﻿"))
    except (ValueError, KeyError) as exc:
        raise IcsError(
            f"that does not look like a calendar feed ({exc}) — check the URL points "
            "at an .ics file rather than a web page."
        ) from exc

    events: list[IcsEvent] = []
    skipped = 0
    seen: set[str] = set()
    for component in calendar.walk("VEVENT"):
        try:
            events.append(_event_from(component, calendar_id=calendar_id, source=source, seen=seen))
        except Exception as exc:  # noqa: BLE001 - one junk entry must not lose the rest
            skipped += 1
            logger.warning("skipping unparseable VEVENT in calendar %s: %s", calendar_id, exc)
    return ParseResult(events=events, skipped=skipped)


def parse(text: str, *, calendar_id: str, source: str = "ics") -> list[IcsEvent]:
    """The events in a .ics document, dropping any that could not be read.

    :class:`IcsEvent` is a :class:`~backend.core.events.CalendarEvent`, so this
    is a ``list[CalendarEvent]`` to every consumer that wants one. Callers that
    need to *report* how much was dropped — the subscription probe, the sync
    job — use :func:`parse_feed` instead.
    """
    return parse_feed(text, calendar_id=calendar_id, source=source).events


# --- export -----------------------------------------------------------------


def render(rows: Iterable[dict[str, Any]], *, name: str = "Argus") -> str:
    """Stored rows as a standard iCalendar document.

    The counterweight to keeping events in SQLite. They are invisible in
    Obsidian and outside the vault's git snapshots, so without an export they
    would be the one thing in Argus the user could not take elsewhere — and
    "local-first" that you cannot get your data out of is just a silo with
    better latency.

    Built with :mod:`icalendar` rather than string concatenation because the
    parts that look trivial are not: CRLF line endings, 75-octet line folding
    and escaping in TEXT values are all load-bearing, and a feed that another
    calendar refuses to import is worse than no export at all.
    """
    calendar = Calendar()
    # PRODID and VERSION are both mandatory; an .ics without them is rejected
    # outright by strict importers, Outlook among them.
    calendar.add("prodid", "-//Argus//Local Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", name)

    for row in rows:
        event = Event()
        event.add("uid", row["id"])
        event.add("summary", row.get("title") or UNTITLED)
        all_day = bool(row.get("all_day"))
        start = _parse_stored(row.get("start"), all_day)
        end = _parse_stored(row.get("end"), all_day)
        if start is None:
            continue
        event.add("dtstart", start)
        if end is not None:
            # An all-day DTEND is exclusive on the wire, so a one-day event
            # ends the following day. Storing it inclusive and converting here
            # keeps the off-by-one in one place instead of two.
            event.add("dtend", end + timedelta(days=1) if all_day else end)
        if row.get("location"):
            event.add("location", row["location"])
        if row.get("notes"):
            event.add("description", row["notes"])
        if row.get("rrule"):
            event.add("rrule", vRecur.from_ical(row["rrule"]))
        calendar.add_component(event)

    return calendar.to_ical().decode("utf-8")


def _parse_stored(value: Any, all_day: bool) -> date | datetime | None:
    """One stored ISO string back into the type iCalendar needs."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]) if all_day else datetime.fromisoformat(value)
    except ValueError:
        return None


def feed_name(text: str) -> str | None:
    """The calendar's own name, when the feed publishes one.

    Google, Outlook and most timetable exporters set ``X-WR-CALNAME``. Using
    it to prefill the subscribe dialog means the user confirms a name rather
    than inventing one, and the name they end up with matches what the same
    calendar is called everywhere else.
    """
    try:
        calendar = Calendar.from_ical(text)
    except Exception:  # noqa: BLE001 - a name is a nicety, never a failure
        return None
    raw = calendar.get("X-WR-CALNAME")
    name = str(raw).strip() if raw else ""
    return name or None
