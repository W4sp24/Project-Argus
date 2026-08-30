"""Seed calendar state directly into a throwaway vault -- no network, no keyring.

Same reasoning as seed_automations.py: the CI `e2e` job deliberately installs
no `keyrings.alt`, so subscribing through the UI would write a credential and
fail there while passing locally. And a subscription's *fetch* would need a
live .ics endpoint, which an e2e run must never depend on. Both the keyring
path and the HTTP path are covered by pytest with a fake keyring and
`httpx.MockTransport`; what the browser needs is the state those produce.

So this writes rows through `backend.features.calendar.store` directly:

- a **local** event today, to prove the zero-setup path renders;
- a **weekly recurring** event, because expansion happens on read and a spec
  that only seeds one-offs would never exercise it;
- a **subscribed** (`kind='ics'`) calendar with one event, which is how the
  read-only marker gets something to mark. Its `url_ref` points at a keyring
  entry that does not exist -- deliberately, since nothing here syncs, and an
  unresolvable ref is exactly the state CI's absent keyring produces.

Dates are anchored to *today* rather than fixed, because the calendar page
opens on the current month and a fixed date would fall out of the default
view the moment the suite outlived it.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from backend.core.db import connect, init_schema
from backend.features.calendar import store

#: Titles the spec locates by. Distinctive on purpose: Playwright's
#: accessible-name matching is a case-insensitive *substring*, so a title like
#: "Event" would collide with half the buttons on the page.
LOCAL_TITLE = "Seeded dentist visit"
RECURRING_TITLE = "Seeded weekly standup"
SUBSCRIBED_TITLE = "Seeded lecture from feed"
SUBSCRIBED_CALENDAR = "Seeded timetable"


def main(vault: Path) -> None:
    conn = connect(vault / ".argus" / "argus.db")
    init_schema(conn)
    try:
        store.ensure_default_calendar(conn)
        today = date.today()

        store.upsert_event(
            conn,
            store.DEFAULT_CALENDAR_ID,
            event_id="seed-local-1",
            title=LOCAL_TITLE,
            start=f"{today.isoformat()}T14:00:00",
            end=f"{today.isoformat()}T15:00:00",
            location="Clinic",
        )

        # Anchored a fortnight back so the current month shows several
        # occurrences, not just the anchor itself.
        anchor = today - timedelta(days=14)
        store.upsert_event(
            conn,
            store.DEFAULT_CALENDAR_ID,
            event_id="seed-recurring-1",
            title=RECURRING_TITLE,
            start=f"{anchor.isoformat()}T09:00:00",
            end=f"{anchor.isoformat()}T09:15:00",
            rrule="FREQ=WEEKLY",
        )

        feed = store.create_calendar(
            conn,
            name=SUBSCRIBED_CALENDAR,
            calendar_id="seed-feed",
            kind="ics",
            url_ref="calendar:seed-feed",
            url_display="timetable.example.edu/…",
        )
        store.upsert_event(
            conn,
            feed["id"],
            event_id="seed-feed-1",
            title=SUBSCRIBED_TITLE,
            start=f"{today.isoformat()}T11:00:00",
            end=f"{today.isoformat()}T12:30:00",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
