"""Tests for backend/features/calendar/ics.py: fetching and parsing .ics feeds.

Every fetch case runs against ``httpx.MockTransport`` — no live feed, no
network, no sleeping — matching ``tests/features/automations/test_n8n_client.py``.

The .ics fixtures below are deliberately written the way real publishers emit
them (CRLF-free triple quotes where it doesn't matter, explicit ``\\r\\n``
folding where it does, Google's property ordering and ``X-WR-CALNAME``
preamble), because the bugs this module exists to avoid — a folded SUMMARY
truncated at column 75, an all-day event rendered as two days, one junk VEVENT
taking the whole feed with it — only reproduce against realistic input.
"""

from __future__ import annotations

import httpx
import pytest

from backend.core.events import CalendarEvent
from backend.features.calendar.ics import (
    EXDATE_SEPARATOR,
    MAX_RESPONSE_BYTES,
    IcsError,
    fetch,
    parse,
    parse_feed,
)

CAL_ID = "cal-123"
FEED_URL = "https://calendar.google.com/calendar/ical/abc%40group/private-secret/basic.ics"


# --- fixtures ---------------------------------------------------------------


def client_for(handle) -> httpx.Client:
    """An httpx client whose every request is answered by ``handle``."""
    return httpx.Client(transport=httpx.MockTransport(handle))


def ics_response(body: str, **headers: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/calendar", **headers})


#: A single timed event, as Google Calendar's "secret address in iCal format"
#: export writes it.
GOOGLE_TIMED = """BEGIN:VCALENDAR
PRODID:-//Google Inc//Google Calendar 70.9054//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:ethan@example.com
X-WR-TIMEZONE:Europe/London
BEGIN:VEVENT
DTSTART:20260901T090000Z
DTEND:20260901T103000Z
DTSTAMP:20260830T120000Z
UID:1a2b3c@google.com
CREATED:20260801T101500Z
DESCRIPTION:Bring the slide deck.
LAST-MODIFIED:20260815T101500Z
LOCATION:Meeting Room 3
SEQUENCE:0
STATUS:CONFIRMED
SUMMARY:Design review
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

#: Two all-day events: a one-day one and a three-day one. In both, iCalendar's
#: DTEND is the *exclusive* day after the last.
ALL_DAY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:one-day@example.com
SUMMARY:Public holiday
DTSTART;VALUE=DATE:20260903
DTEND;VALUE=DATE:20260904
END:VEVENT
BEGIN:VEVENT
UID:three-day@example.com
SUMMARY:Conference
DTSTART;VALUE=DATE:20260910
DTEND;VALUE=DATE:20260913
END:VEVENT
BEGIN:VEVENT
UID:no-end@example.com
SUMMARY:Deadline
DTSTART;VALUE=DATE:20260915
END:VEVENT
END:VCALENDAR
"""

#: RFC 5545 lets an event carry DURATION instead of DTEND, or neither.
DURATION_AND_OPEN_ENDED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:duration@example.com
SUMMARY:Lab session
DTSTART:20260905T140000Z
DURATION:PT1H30M
END:VEVENT
BEGIN:VEVENT
UID:open-ended@example.com
SUMMARY:Reminder
DTSTART:20260906T140000Z
END:VEVENT
END:VCALENDAR
"""

#: A weekly series with two cancelled instances, on two EXDATE lines (Google's
#: shape) — plus a second series putting both on one line (Outlook's shape).
RECURRING = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:weekly@example.com
SUMMARY:Standup
DTSTART:20260907T090000Z
DTEND:20260907T093000Z
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=5
EXDATE:20260914T090000Z
EXDATE:20260921T090000Z
END:VEVENT
BEGIN:VEVENT
UID:daily@example.com
SUMMARY:Reading
DTSTART:20260907T190000Z
DTEND:20260907T193000Z
RRULE:FREQ=DAILY;COUNT=4
EXDATE:20260908T190000Z,20260909T190000Z
END:VEVENT
END:VCALENDAR
"""

#: Three events; the middle one's DTSTART is junk. icalendar 6 drops an
#: unparseable property value rather than raising, so the broken event arrives
#: as a VEVENT with no DTSTART at all.
MIXED_WITH_JUNK = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:good-1@example.com
SUMMARY:Before
DTSTART:20260901T090000Z
DTEND:20260901T100000Z
END:VEVENT
BEGIN:VEVENT
UID:broken@example.com
SUMMARY:Corrupt
DTSTART:NOT-A-DATE
END:VEVENT
BEGIN:VEVENT
UID:good-2@example.com
SUMMARY:After
DTSTART:20260902T090000Z
DTEND:20260902T100000Z
END:VEVENT
END:VCALENDAR
"""

LONG_SUMMARY = (
    "Quantum mechanics lecture 7: perturbation theory and the variational "
    "principle, with worked examples"
)

#: A SUMMARY folded across three physical lines at the 75-octet limit, with
#: real CRLF endings. Unfolding is icalendar's job; that it *happens* is this
#: module's contract, because a truncated title is the classic ICS bug.
FOLDED = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:folded@example.com\r\n"
    "SUMMARY:Quantum mechanics lecture 7: perturbation theory and the variatio\r\n"
    " nal principle\\, with worked\r\n"
    "  examples\r\n"
    "DTSTART:20260901T090000Z\r\n"
    "DTEND:20260901T110000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

#: A local-time event with a TZID and no VTIMEZONE block — 09:00 London in
#: September is 08:00 UTC.
TZID_LOCAL = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:london@example.com
SUMMARY:Tutorial
DTSTART;TZID=Europe/London:20260901T090000
DTEND;TZID=Europe/London:20260901T100000
END:VEVENT
END:VCALENDAR
"""

EMPTY_CALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
END:VCALENDAR
"""


# --- fetch: scheme handling -------------------------------------------------


def test_webcal_url_is_fetched_over_https() -> None:
    """Apple and Google hand out ``webcal://`` links; httpx cannot fetch that."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return ics_response(GOOGLE_TIMED)

    with client_for(handle) as client:
        body, _etag = fetch("webcal://example.com/feed.ics", client=client)

    assert seen == ["https://example.com/feed.ics"]
    assert body is not None
    assert "Design review" in body


def test_webcal_normalisation_is_case_insensitive() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return ics_response(EMPTY_CALENDAR)

    with client_for(handle) as client:
        fetch("WEBCAL://example.com/feed.ics", client=client)

    assert seen == ["https://example.com/feed.ics"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Users/ethan/Documents/secrets.txt",
        "file:///etc/passwd",
        "ftp://example.com/feed.ics",
        "data:text/calendar,BEGIN:VCALENDAR",
        "/etc/passwd",
    ],
)
def test_non_http_schemes_are_rejected_without_a_request(url: str) -> None:
    """The URL is user-supplied and the *backend* fetches it."""
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ics_response(EMPTY_CALENDAR)

    with (
        client_for(handle) as client,
        pytest.raises(IcsError) as excinfo,
    ):
        fetch(url, client=client)

    assert calls == []
    assert "http" in str(excinfo.value).lower()


def test_plain_http_is_allowed() -> None:
    with client_for(lambda request: ics_response(EMPTY_CALENDAR)) as client:
        body, _etag = fetch("http://localhost:8080/feed.ics", client=client)
    assert body is not None


# --- fetch: caching ---------------------------------------------------------


def test_etag_is_returned_for_next_time() -> None:
    with client_for(lambda r: ics_response(GOOGLE_TIMED, etag='W/"v1"')) as client:
        body, etag = fetch(FEED_URL, client=client)

    assert etag == 'W/"v1"'
    assert body is not None


def test_stored_etag_is_sent_as_if_none_match() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        return ics_response(GOOGLE_TIMED, etag='W/"v2"')

    with client_for(handle) as client:
        fetch(FEED_URL, etag='W/"v1"', client=client)

    assert seen == ['W/"v1"']


def test_no_etag_means_no_if_none_match_header() -> None:
    seen: list[bool] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append("if-none-match" in request.headers)
        return ics_response(GOOGLE_TIMED)

    with client_for(handle) as client:
        fetch(FEED_URL, client=client)

    assert seen == [False]


def test_304_returns_none_body_and_keeps_the_etag() -> None:
    """``(None, etag)`` is the caller's signal to keep its cached events."""
    with client_for(lambda r: httpx.Response(304)) as client:
        body, etag = fetch(FEED_URL, etag='W/"v1"', client=client)

    assert body is None
    assert etag == 'W/"v1"'


def test_304_prefers_a_freshly_issued_etag() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, headers={"etag": 'W/"v2"'})

    with client_for(handle) as client:
        body, etag = fetch(FEED_URL, etag='W/"v1"', client=client)

    assert body is None
    assert etag == 'W/"v2"'


def test_missing_etag_header_returns_none() -> None:
    with client_for(lambda r: ics_response(GOOGLE_TIMED)) as client:
        _body, etag = fetch(FEED_URL, client=client)
    assert etag is None


# --- fetch: errors ----------------------------------------------------------


def test_404_does_not_read_as_an_empty_calendar() -> None:
    """A rotated secret URL 404s; the message must say so, not "no events"."""
    with (
        client_for(lambda r: httpx.Response(404, text="Not Found")) as client,
        pytest.raises(IcsError) as excinfo,
    ):
            fetch(FEED_URL, client=client)

    message = str(excinfo.value)
    assert "404" in message
    assert "empty" not in message.lower()


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_distinguished_from_a_missing_feed(status: int) -> None:
    with (
        client_for(lambda r: httpx.Response(status, text="Denied")) as client,
        pytest.raises(IcsError) as excinfo,
    ):
            fetch(FEED_URL, client=client)

    assert str(status) in str(excinfo.value)

    with (
        client_for(lambda r: httpx.Response(404)) as client,
        pytest.raises(IcsError) as not_found,
    ):
            fetch(FEED_URL, client=client)

    assert str(excinfo.value) != str(not_found.value)


def test_server_error_raises_ics_error() -> None:
    with (
        client_for(lambda r: httpx.Response(503, text="upstream down")) as client,
        pytest.raises(IcsError) as excinfo,
    ):
            fetch(FEED_URL, client=client)
    assert "503" in str(excinfo.value)


def test_transport_failure_raises_ics_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed", request=request)

    with (
        client_for(handle) as client,
        pytest.raises(IcsError) as excinfo,
    ):
        fetch(FEED_URL, client=client)

    assert "example" in str(excinfo.value) or "calendar.google.com" in str(excinfo.value)


# --- fetch: redirects and size ----------------------------------------------


def test_redirects_are_followed() -> None:
    """Feed URLs redirect constantly (webcal aliases, CDN fronts, tinyurls)."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/feed.ics":
            return httpx.Response(302, headers={"location": "https://example.com/real.ics"})
        return ics_response(GOOGLE_TIMED)

    with client_for(handle) as client:
        body, _etag = fetch("https://example.com/feed.ics", client=client)

    assert seen == ["/feed.ics", "/real.ics"]
    assert body is not None
    assert "Design review" in body


def test_oversized_response_is_rejected() -> None:
    oversized = "X" * (MAX_RESPONSE_BYTES + 1)

    with (
        client_for(lambda r: ics_response(oversized)) as client,
        pytest.raises(IcsError) as excinfo,
    ):
            fetch(FEED_URL, client=client)

    assert "too large" in str(excinfo.value).lower()


def test_response_under_the_cap_is_accepted() -> None:
    with client_for(lambda r: ics_response(GOOGLE_TIMED)) as client:
        body, _etag = fetch(FEED_URL, client=client)
    assert body == GOOGLE_TIMED


def test_utf8_body_is_decoded() -> None:
    body_text = GOOGLE_TIMED.replace("Design review", "Café résumé — naïve")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body_text.encode("utf-8"), headers={"content-type": "text/calendar"}
        )

    with client_for(handle) as client:
        body, _etag = fetch(FEED_URL, client=client)

    assert body is not None
    assert "Café résumé — naïve" in body


def test_byte_order_mark_is_stripped() -> None:
    """Outlook exports carry a BOM, and icalendar refuses to parse past one."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\xef\xbb\xbf" + GOOGLE_TIMED.encode("utf-8"),
            headers={"content-type": "text/calendar"},
        )

    with client_for(handle) as client:
        body, _etag = fetch(FEED_URL, client=client)

    assert body is not None
    assert body.startswith("BEGIN:VCALENDAR")
    assert len(parse(body, calendar_id=CAL_ID)) == 1


# --- parse: field mapping ---------------------------------------------------


def test_timed_event_maps_every_field() -> None:
    (event,) = parse(GOOGLE_TIMED, calendar_id=CAL_ID)

    assert event.title == "Design review"
    assert event.start == "2026-09-01T09:00:00+00:00"
    assert event.end == "2026-09-01T10:30:00+00:00"
    assert event.all_day is False
    assert event.location == "Meeting Room 3"
    assert event.notes == "Bring the slide deck."
    assert event.id == "1a2b3c@google.com"
    assert event.calendar_id == CAL_ID
    assert event.source == "ics"


def test_parsed_events_are_calendar_events() -> None:
    """The extra recurrence fields ride on a subclass, so consumers typed
    against ``CalendarEvent`` keep working unedited."""
    (event,) = parse(GOOGLE_TIMED, calendar_id=CAL_ID)
    assert isinstance(event, CalendarEvent)


def test_subscribed_events_are_never_editable() -> None:
    """An .ics subscription is read-only by protocol."""
    events = parse(RECURRING, calendar_id=CAL_ID) + parse(ALL_DAY, calendar_id=CAL_ID)
    assert events
    assert all(event.editable is False for event in events)


def test_source_is_overridable() -> None:
    (event,) = parse(GOOGLE_TIMED, calendar_id=CAL_ID, source="holidays")
    assert event.source == "holidays"


def test_tzid_local_times_are_normalised_to_utc() -> None:
    """The store compares ``start`` as a string, which only sorts correctly
    when every row shares one offset."""
    (event,) = parse(TZID_LOCAL, calendar_id=CAL_ID)
    assert event.start == "2026-09-01T08:00:00+00:00"
    assert event.end == "2026-09-01T09:00:00+00:00"


def test_untitled_event_gets_the_repo_placeholder() -> None:
    text = GOOGLE_TIMED.replace("SUMMARY:Design review\n", "")
    (event,) = parse(text, calendar_id=CAL_ID)
    assert event.title == "(untitled)"


def test_folded_summary_is_unfolded_whole() -> None:
    (event,) = parse(FOLDED, calendar_id=CAL_ID)
    assert event.title == LONG_SUMMARY


# --- parse: all-day ---------------------------------------------------------


def test_all_day_events_are_date_only_with_an_exclusive_end() -> None:
    """DTEND stays exclusive, matching Google's own wire format (and
    ``connectors/gcal.py``, which passes Google's exclusive date straight
    through). A one-day event is 09-03 → 09-04, not 09-03 → 09-03."""
    one_day, three_day, deadline = parse(ALL_DAY, calendar_id=CAL_ID)

    assert (one_day.all_day, one_day.start, one_day.end) == (True, "2026-09-03", "2026-09-04")
    assert (three_day.start, three_day.end) == ("2026-09-10", "2026-09-13")
    assert deadline.all_day is True


def test_all_day_without_dtend_lasts_one_day() -> None:
    _one, _three, deadline = parse(ALL_DAY, calendar_id=CAL_ID)
    assert (deadline.start, deadline.end) == ("2026-09-15", "2026-09-16")


# --- parse: DTEND / DURATION ------------------------------------------------


def test_duration_is_added_to_dtstart_when_dtend_is_absent() -> None:
    lab, _reminder = parse(DURATION_AND_OPEN_ENDED, calendar_id=CAL_ID)
    assert lab.start == "2026-09-05T14:00:00+00:00"
    assert lab.end == "2026-09-05T15:30:00+00:00"


def test_timed_event_with_neither_dtend_nor_duration_is_zero_length() -> None:
    _lab, reminder = parse(DURATION_AND_OPEN_ENDED, calendar_id=CAL_ID)
    assert reminder.end == reminder.start


# --- parse: recurrence carried, not expanded --------------------------------


def test_rrule_is_carried_through_unexpanded() -> None:
    """Expansion belongs to ``recurrence.py``; this module must not consume
    the rule by expanding it, or a five-week series becomes five rows that no
    edit can ever re-collapse."""
    weekly, daily = parse(RECURRING, calendar_id=CAL_ID)

    assert sorted(weekly.rrule.split(";")) == ["BYDAY=MO", "COUNT=5", "FREQ=WEEKLY"]
    assert sorted(daily.rrule.split(";")) == ["COUNT=4", "FREQ=DAILY"]
    # One row per series, not one per instance.
    assert len(parse(RECURRING, calendar_id=CAL_ID)) == 2
    assert weekly.start == "2026-09-07T09:00:00+00:00"


def test_exdates_from_repeated_properties_are_carried() -> None:
    weekly, _daily = parse(RECURRING, calendar_id=CAL_ID)
    assert weekly.exdates.split(EXDATE_SEPARATOR) == [
        "2026-09-14T09:00:00+00:00",
        "2026-09-21T09:00:00+00:00",
    ]


def test_exdates_from_one_multi_value_property_are_carried() -> None:
    _weekly, daily = parse(RECURRING, calendar_id=CAL_ID)
    assert daily.exdates.split(EXDATE_SEPARATOR) == [
        "2026-09-08T19:00:00+00:00",
        "2026-09-09T19:00:00+00:00",
    ]


def test_non_recurring_events_carry_empty_recurrence_fields() -> None:
    """``calendar_events.exdates`` is ``TEXT NOT NULL DEFAULT ''``."""
    (event,) = parse(GOOGLE_TIMED, calendar_id=CAL_ID)
    assert event.rrule is None
    assert event.exdates == ""


def test_recurrence_id_overrides_do_not_collide_with_their_series() -> None:
    """``store.replace_events`` INSERTs without an upsert, so two rows sharing
    a UID would abort the whole sync transaction."""
    text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:series@example.com
SUMMARY:Standup
DTSTART:20260907T090000Z
DTEND:20260907T093000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:series@example.com
RECURRENCE-ID:20260908T090000Z
SUMMARY:Standup (moved)
DTSTART:20260908T110000Z
DTEND:20260908T113000Z
END:VEVENT
END:VCALENDAR
"""
    events = parse(text, calendar_id=CAL_ID)
    assert len(events) == 2
    assert len({event.id for event in events}) == 2


# --- parse: resilience ------------------------------------------------------


def test_one_malformed_vevent_does_not_lose_the_feed() -> None:
    result = parse_feed(MIXED_WITH_JUNK, calendar_id=CAL_ID)

    assert [event.title for event in result.events] == ["Before", "After"]
    assert result.skipped == 1


def test_parse_returns_only_the_events_that_survived() -> None:
    assert len(parse(MIXED_WITH_JUNK, calendar_id=CAL_ID)) == 2


def test_clean_feed_reports_nothing_skipped() -> None:
    result = parse_feed(GOOGLE_TIMED, calendar_id=CAL_ID)
    assert result.skipped == 0
    assert len(result.events) == 1


def test_empty_calendar_parses_to_no_events() -> None:
    result = parse_feed(EMPTY_CALENDAR, calendar_id=CAL_ID)
    assert result.events == []
    assert result.skipped == 0


def test_unparseable_document_raises_ics_error() -> None:
    """A whole-document failure is *not* a skip: reporting it as "0 events"
    would show an empty calendar for a feed that is actually broken."""
    with pytest.raises(IcsError):
        parse("<html>Sign in to continue</html>", calendar_id=CAL_ID)


def test_empty_body_raises_ics_error() -> None:
    with pytest.raises(IcsError):
        parse("", calendar_id=CAL_ID)


def test_parse_strips_a_byte_order_mark() -> None:
    (event,) = parse("\ufeff" + GOOGLE_TIMED, calendar_id=CAL_ID)
    assert event.title == "Design review"
