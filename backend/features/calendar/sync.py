"""Pulling a subscribed .ics feed into the local store.

One job, three steps: read the feed URL out of the keyring, fetch it, replace
that calendar's rows with what came back. `ics` owns the protocol, `store`
owns the rows; this owns the *outcome* — what happened, and whether the user
can tell.

**Failure has to be visible.** A subscription is a background job nobody
watches, so the two ways it can rot are both silent: the secret URL gets
rotated and starts 404ing, or the feed starts returning junk. Either way the
calendar keeps rendering yesterday's events and looks fine. So every attempt
writes its outcome to `last_sync_at` / `last_sync_error` whether it succeeded
or not, and the UI reads them. A sync that fails must never look like a
calendar with nothing in it.

**A failed sync keeps the old events.** `record_sync` is called instead of
`replace_events`, not as well as it — stale data the user can see beats an
empty grid they cannot explain.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from backend.features.calendar import ics, store

logger = logging.getLogger("argus.calendar.sync")

#: Keyring ref prefix for a subscription's URL. The URL *is* the credential —
#: anyone holding it can read the calendar — so it lives in the OS keyring
#: (invariant I4) and never in SQLite, which only keeps a redacted display
#: form. Namespaced like every other ref in `agent.credentials`.
KEY_REF_PREFIX = "calendar"


def key_ref_for(calendar_id: str) -> str:
    """The keyring ref holding ``calendar_id``'s feed URL."""
    return f"{KEY_REF_PREFIX}:{calendar_id}"


def redact(url: str) -> str:
    """A feed URL with its secret path removed, safe to store and display.

    Google's "secret address in iCal format" carries the secret in the *path*,
    not a query parameter, so showing the URL anywhere — a list row, a log
    line, an error message — hands over read access to the whole calendar.
    Host plus a marker is enough for a user to recognise which subscription a
    row is.
    """
    try:
        parsed = httpx.URL(url)
    except Exception:  # noqa: BLE001 - display text must not raise
        return "(hidden)"
    host = parsed.host or "(unknown)"
    return f"{host}/…" if parsed.path not in ("", "/") else host


class SyncError(RuntimeError):
    """A sync attempt failed. The message is shown to the user verbatim."""


def sync_calendar(
    conn: sqlite3.Connection,
    calendar_id: str,
    *,
    client: httpx.Client | None = None,
    get_url: Callable[[str], str | None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Refresh one subscribed calendar. Returns its row, sync state included.

    Never raises for anything the user can cause: this runs on a scheduler
    thread where an exception is a stack trace in a log nobody reads. The
    outcome goes on the row instead, where the UI shows it.

    ``now`` is forwarded only when given rather than defaulted here, so the
    store stays the single owner of what "now" means — two modules with their
    own clock default is two places to change when one of them is wrong.
    """
    kwargs: dict[str, Any] = {} if now is None else {"now": now}
    calendar = store.get_calendar(conn, calendar_id)
    if calendar is None:
        raise store.CalendarNotFound(calendar_id)
    if calendar.get("kind") != "ics":
        # A local calendar has no upstream; syncing one is a caller bug, not a
        # user-visible failure, so this is the one case that does raise.
        raise SyncError(f"calendar {calendar_id} is not a subscription")

    read_url = get_url or _keyring_url
    try:
        url = read_url(calendar_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable keyring is a sync failure
        logger.warning("could not read feed URL for %s", calendar_id, exc_info=True)
        return store.record_sync(conn, calendar_id, etag=None, error=str(exc), **kwargs)

    if not url:
        return store.record_sync(
            conn,
            calendar_id,
            etag=None,
            # Distinct from a fetch failure: the secret is gone from the
            # keyring, so re-syncing will never fix it and the UI should say
            # "reconnect", not "retrying".
            error="no feed URL stored — reconnect this calendar",
            **kwargs,
        )

    try:
        body, etag = ics.fetch(url, etag=calendar.get("etag"), client=client)
    except ics.IcsError as exc:
        logger.info("feed fetch failed for %s: %s", calendar_id, exc)
        return store.record_sync(conn, calendar_id, etag=None, error=str(exc), **kwargs)

    if body is None:
        # 304: the feed is unchanged, so the rows already stored are current.
        # Recording success matters as much as writing rows would — without
        # it a calendar that legitimately never changes would age into looking
        # stale and then broken.
        return store.record_sync(conn, calendar_id, etag=etag, error=None, **kwargs)

    try:
        parsed = ics.parse_feed(body, calendar_id=calendar_id)
    except Exception as exc:  # noqa: BLE001 - a malformed feed is not a crash
        logger.info("feed parse failed for %s: %s", calendar_id, exc)
        return store.record_sync(
            conn, calendar_id, etag=None, error=f"could not read the feed: {exc}", **kwargs
        )

    try:
        store.replace_events(
            conn, calendar_id, [event.model_dump() for event in parsed.events], **kwargs
        )
    except sqlite3.Error as exc:
        logger.exception("storing feed events failed for %s", calendar_id)
        return store.record_sync(conn, calendar_id, etag=None, error=str(exc), **kwargs)

    if parsed.skipped:
        logger.info("%s: %d unusable entries skipped", calendar_id, parsed.skipped)
    return store.record_sync(conn, calendar_id, etag=etag, error=None, **kwargs)


def sync_all(
    conn: sqlite3.Connection, *, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Refresh every enabled subscription. Used by the hourly job."""
    results: list[dict[str, Any]] = []
    for calendar in store.list_calendars(conn):
        if calendar.get("kind") != "ics" or not calendar.get("enabled"):
            continue
        try:
            results.append(sync_calendar(conn, calendar["id"], client=client))
        except Exception:  # noqa: BLE001 - one bad subscription must not stop the rest
            logger.exception("sync failed for %s", calendar.get("id"))
    return results


def _keyring_url(calendar_id: str) -> str | None:
    from backend.agent.credentials import get_key

    return get_key(key_ref_for(calendar_id))
