"""Google Calendar (read-only in P2).

Setup is a human step: create a Desktop OAuth client in Google Cloud Console,
save ``credentials.json`` in the repo root (gitignored), then run
``argus connect gcal`` once to complete the browser consent flow. The token
is stored in the OS keyring (invariant I4).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel

# Read events in P2; insert approved schedule blocks in P3 (writer-gated, I1).
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
ARGUS_COLOR_ID = "3"  # grape — Argus-created blocks are visually distinct
CREDENTIALS_FILE = Path("credentials.json")
KEYRING_SERVICE = "argus-gcal"
KEYRING_USER = "token"


class CalendarEvent(BaseModel):
    """One calendar event in Argus's agenda shape."""

    title: str
    start: str
    end: str
    all_day: bool = False
    source: str = "gcal"


def _stored_token() -> str | None:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def configured() -> bool:
    """True when a usable OAuth token is stored."""
    try:
        return _stored_token() is not None
    except Exception:
        return False


def connect(credentials_file: Path = CREDENTIALS_FILE) -> None:
    """Run the one-time browser consent flow and store the token (I4)."""
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not credentials_file.is_file():
        raise FileNotFoundError(
            f"{credentials_file} not found — create a Desktop OAuth client in Google Cloud "
            "Console and save its JSON here first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, creds.to_json())


def _service():
    # Check the token BEFORE importing the google client libs: the common
    # case (gcal never connected) must degrade gracefully even when those
    # optional deps aren't installed. Importing first (as this used to)
    # raised ImportError for every /api/agenda + /api/insights call in a
    # fresh venv, regardless of whether gcal was configured (Phase H fix).
    raw = _stored_token()
    if raw is None:
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if creds.expired and creds.refresh_token:
        import keyring
        from google.auth.transport.requests import Request

        creds.refresh(Request())
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def insert_event(title: str, start: str, end: str, service=None) -> None:
    """Insert one Argus block. Only ``backend.writer`` may call this (I1)."""
    service = service or _service()
    if service is None:
        raise RuntimeError("Google Calendar is not connected — run `argus connect gcal`")
    service.events().insert(
        calendarId="primary",
        body={
            "summary": title,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
            "colorId": ARGUS_COLOR_ID,
            "description": "Scheduled by Argus (approved suggestion)",
        },
    ).execute()


def list_events(day: date, service=None) -> list[CalendarEvent]:
    """Events for one local day; [] when unconfigured. ``service`` injectable."""
    service = service or _service()
    if service is None:
        return []
    start = datetime.combine(day, time.min).astimezone()
    end = start + timedelta(days=1)
    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events: list[CalendarEvent] = []
    for item in response.get("items", []):
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        all_day = "date" in start_raw
        events.append(
            CalendarEvent(
                title=item.get("summary", "(untitled)"),
                start=start_raw.get("dateTime") or start_raw.get("date", ""),
                end=end_raw.get("dateTime") or end_raw.get("date", ""),
                all_day=all_day,
            )
        )
    return events
