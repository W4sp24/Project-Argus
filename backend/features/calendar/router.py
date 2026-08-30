"""Calendar endpoints, mounted by ``backend.main.create_app``.

Follows the house router shape: a builder taking ``Settings``, a per-request
sqlite connection, and domain exceptions mapped to HTTP codes.

Two things here are not boilerplate.

**A subscription's URL never comes back out.** It is a credential — Google
puts the secret in the path of its "secret address in iCal format", so
anyone who can read the URL can read the calendar. It goes to the OS keyring
(invariant I4) and the API answers with a redacted host only, the same way
every other connector reports ``key_state`` rather than a key.

**Creating a subscription probes before it persists.** The repo's ordering
rule for anything credentialed: verify, then write the registry row, then
the secret, rolling the row back if the secret cannot be stored. A
subscription that is saved and then found to be unreachable is worse than
one that was never saved, because it renders as an empty calendar rather
than a failed connection.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend.agent.credentials import CredentialError, delete_key, store_key
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.core.events import CalendarEvent
from backend.features.calendar import ics, recurrence, service, store, sync

#: How far either side of an unspecified window to look. A month of context is
#: what the month grid needs and is cheap; callers wanting more say so.
DEFAULT_WINDOW_DAYS = 31


class CalendarInfo(BaseModel):
    """One calendar. Never carries the feed URL — see the module docstring."""

    id: str
    name: str
    kind: str
    color: str = ""
    url_display: str | None = None
    refresh_interval_seconds: int = 3600
    last_sync_at: str | None = None
    last_sync_error: str | None = None
    enabled: bool = True
    created_at: str


class EventRequest(BaseModel):
    title: str = Field(min_length=1)
    start: str
    end: str
    all_day: bool = False
    location: str | None = None
    notes: str | None = None
    rrule: str | None = None
    calendar_id: str | None = None


class EventPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    start: str | None = None
    end: str | None = None
    all_day: bool | None = None
    location: str | None = None
    notes: str | None = None
    rrule: str | None = None


class SubscriptionRequest(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    color: str = ""


class SubscriptionProbe(BaseModel):
    """What a probe found, so the dialog can say more than "ok"."""

    events: int
    skipped: int
    name_hint: str | None = None


def build_calendar_router(
    settings: Settings, *, client_factory=None
) -> APIRouter:
    router = APIRouter(prefix="/api/calendar")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    def _client() -> httpx.Client | None:
        return client_factory() if client_factory is not None else None

    @router.get("/calendars", response_model=list[CalendarInfo])
    def calendars() -> list[CalendarInfo]:
        conn = db()
        try:
            store.ensure_default_calendar(conn)
            return [CalendarInfo(**row) for row in store.list_calendars(conn)]
        finally:
            conn.close()

    @router.get("/events", response_model=list[CalendarEvent])
    def events(
        start: str | None = None,
        end: str | None = None,
        calendar_id: Annotated[list[str] | None, Query()] = None,
    ) -> list[CalendarEvent]:
        window_start = _as_date(start) if start else date.today()
        window_end = (
            _as_date(end) if end else window_start + timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        if window_end <= window_start:
            raise HTTPException(status_code=422, detail="end must be after start")
        conn = db()
        try:
            return service.events_in_window(
                conn, window_start, window_end, calendar_ids=calendar_id
            )
        finally:
            conn.close()

    @router.post("/events", response_model=CalendarEvent, status_code=201)
    def create_event(request: EventRequest) -> CalendarEvent:
        conn = db()
        try:
            store.ensure_default_calendar(conn)
            target = request.calendar_id or store.DEFAULT_CALENDAR_ID
            _require_writable(conn, target)
            row = store.upsert_event(
                conn,
                target,
                title=request.title,
                start=request.start,
                end=request.end,
                all_day=request.all_day,
                location=request.location,
                notes=request.notes,
                rrule=request.rrule,
            )
            return _as_event(row)
        except store.CalendarNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()

    @router.patch("/events/{event_id}", response_model=CalendarEvent)
    def update_event(event_id: str, patch: EventPatch) -> CalendarEvent:
        # An occurrence id addresses one instance of a series; editing it
        # edits the series, which is the only edit this feature offers. The
        # id is split rather than rejected so the UI can PATCH whatever it
        # rendered without knowing which kind of id it is holding.
        row_id, _occurrence = recurrence.split_occurrence_id(event_id)
        conn = db()
        try:
            existing = store.find_event(conn, row_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no event {event_id}")
            _require_writable(conn, existing["calendar_id"])
            merged = {**existing, **patch.model_dump(exclude_unset=True)}
            row = store.upsert_event(
                conn,
                existing["calendar_id"],
                event_id=row_id,
                title=merged["title"],
                start=merged["start"],
                end=merged["end"],
                all_day=bool(merged["all_day"]),
                location=merged["location"],
                notes=merged["notes"],
                rrule=merged["rrule"],
                exdates=merged.get("exdates", ""),
            )
            return _as_event(row)
        finally:
            conn.close()

    @router.delete("/events/{event_id}")
    def delete_event(event_id: str, scope: str = "series") -> dict[str, bool]:
        row_id, occurrence = recurrence.split_occurrence_id(event_id)
        conn = db()
        try:
            existing = store.find_event(conn, row_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no event {event_id}")
            _require_writable(conn, existing["calendar_id"])
            if scope == "one" and occurrence:
                # Cancelling one instance of a series is an EXDATE, not a
                # delete: the series has to survive so next week still happens.
                store.upsert_event(
                    conn,
                    existing["calendar_id"],
                    event_id=row_id,
                    title=existing["title"],
                    start=existing["start"],
                    end=existing["end"],
                    all_day=bool(existing["all_day"]),
                    location=existing["location"],
                    notes=existing["notes"],
                    rrule=existing["rrule"],
                    exdates=_with_exdate(existing.get("exdates", ""), occurrence),
                )
            else:
                store.delete_event(conn, existing["calendar_id"], row_id)
            return {"ok": True}
        finally:
            conn.close()

    @router.post("/subscriptions/probe", response_model=SubscriptionProbe)
    def probe(request: SubscriptionRequest) -> SubscriptionProbe:
        """Fetch and parse a feed without saving anything."""
        body, _etag = _fetch_or_422(request.url, client=_client())
        parsed = ics.parse_feed(body or "", calendar_id="probe")
        return SubscriptionProbe(events=len(parsed.events), skipped=parsed.skipped)

    @router.post("/subscriptions", response_model=CalendarInfo, status_code=201)
    def subscribe(request: SubscriptionRequest) -> CalendarInfo:
        # Probe first: a saved-but-unreachable subscription renders as an
        # empty calendar, which is indistinguishable from a working one that
        # happens to have nothing in it.
        _fetch_or_422(request.url, client=_client())

        conn = db()
        try:
            row = store.create_calendar(
                conn,
                name=request.name,
                kind="ics",
                color=request.color,
                url_display=sync.redact(request.url),
            )
            try:
                store_key(sync.key_ref_for(row["id"]), request.url)
            except CredentialError as exc:
                # Registry row first, then the secret, rolling the row back if
                # the secret will not store — an orphaned calendar with no URL
                # can never sync and only shows up as a broken one.
                store.delete_calendar(conn, row["id"])
                raise HTTPException(
                    status_code=500, detail=f"could not store the feed URL: {exc}"
                ) from exc
            synced = sync.sync_calendar(conn, row["id"], client=_client())
            return CalendarInfo(**synced)
        finally:
            conn.close()

    @router.post("/subscriptions/{calendar_id}/sync", response_model=CalendarInfo)
    def sync_one(calendar_id: str) -> CalendarInfo:
        conn = db()
        try:
            return CalendarInfo(**sync.sync_calendar(conn, calendar_id, client=_client()))
        except store.CalendarNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except sync.SyncError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()

    @router.delete("/subscriptions/{calendar_id}")
    def unsubscribe(calendar_id: str) -> dict[str, bool]:
        if calendar_id == store.DEFAULT_CALENDAR_ID:
            raise HTTPException(
                status_code=422, detail="the local calendar cannot be removed"
            )
        conn = db()
        try:
            if store.get_calendar(conn, calendar_id) is None:
                raise HTTPException(status_code=404, detail=f"no calendar {calendar_id}")
            # Keyring first, then the row: a secret that outlives its owner is
            # unreachable by any code path and can only be cleaned up by hand.
            delete_key(sync.key_ref_for(calendar_id))
            store.delete_calendar(conn, calendar_id)
            return {"ok": True}
        finally:
            conn.close()

    @router.get("/export.ics")
    def export_ics() -> Response:
        """Every local event as standard iCalendar.

        Local events live in SQLite, so they are invisible in Obsidian and do
        not ride the vault's git snapshots the way notes do. This is the
        promise that they are still the user's to take elsewhere.
        """
        conn = db()
        try:
            rows = [
                row
                for row in store.candidates_for_window(conn, "0000", "9999")
                if row["calendar_id"] == store.DEFAULT_CALENDAR_ID
            ]
        finally:
            conn.close()
        return Response(
            content=ics.render(rows),
            media_type="text/calendar",
            headers={"Content-Disposition": 'attachment; filename="argus.ics"'},
        )

    return router


def _as_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"not a date: {value!r}") from exc


def _as_event(row: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        id=row["id"],
        calendar_id=row["calendar_id"],
        title=row["title"],
        start=row["start"],
        end=row["end"],
        all_day=bool(row["all_day"]),
        location=row["location"],
        notes=row["notes"],
        source=recurrence.DEFAULT_SOURCE,
        editable=True,
    )


def _with_exdate(existing: str, occurrence: str) -> str:
    parts = [part for part in existing.split(recurrence.EXDATE_SEPARATOR) if part]
    if occurrence not in parts:
        parts.append(occurrence)
    return recurrence.EXDATE_SEPARATOR.join(parts)


def _require_writable(conn: sqlite3.Connection, calendar_id: str) -> None:
    """Refuse an edit to a subscribed calendar, loudly.

    An .ics subscription is read-only by protocol. Accepting the write and
    dropping it at the next sync would be the silent kind of wrong: the event
    appears, the user trusts it, and it vanishes an hour later.
    """
    calendar = store.get_calendar(conn, calendar_id)
    if calendar is None:
        raise HTTPException(status_code=404, detail=f"no calendar {calendar_id}")
    if calendar.get("kind") == "ics":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{calendar['name']} is a subscribed calendar and is read-only here — "
                "edit it where it is published"
            ),
        )


def _fetch_or_422(url: str, *, client: httpx.Client | None) -> tuple[str | None, str | None]:
    try:
        return ics.fetch(url, client=client)
    except ics.IcsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
