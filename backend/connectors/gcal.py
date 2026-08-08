"""Google Calendar (read-only in P2).

Setup is a human step: create a Desktop OAuth client in Google Cloud Console,
then hand its JSON to Argus. The normal path is uploading it in-app
(Settings > Integrations), which saves it to the vault's ``.argus/`` dir —
see `Settings.gcal_credentials_file`. For existing setups, dropping a
``credentials.json`` beside the env file (the repo root's ``.env`` in dev,
the Electron shell's userData dir when packaged) still works — see
`legacy_credentials_file`. Either way, run ``argus connect gcal`` once (or
click Connect in-app) to complete the browser consent flow. The token is
stored in the OS keyring (invariant I4).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel

from backend.connectors import ConnectorUnavailable

# Read events in P2; insert approved schedule blocks in P3 (writer-gated, I1).
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
ARGUS_COLOR_ID = "3"  # grape — Argus-created blocks are visually distinct
KEYRING_SERVICE = "argus-gcal"
KEYRING_USER = "token"


def legacy_credentials_file() -> Path:
    """Where a hand-placed ``credentials.json`` is still honored.

    See `Settings.gcal_legacy_credentials_file` for why this is anchored to
    the env file's directory rather than the process's current working
    directory. `Settings` is imported lazily here, not at module scope: the
    layering rule (README.md, "Project layout") puts `backend.core` below
    `backend.connectors`, so a module-level import would be legal, but a
    function-local one keeps this module importable standalone and avoids
    paying for `Settings`'s own imports unless a caller actually needs the
    legacy path.
    """
    from backend.core.config import Settings

    return Settings().gcal_legacy_credentials_file


class CalendarEvent(BaseModel):
    """One calendar event in Argus's agenda shape."""

    title: str
    start: str
    end: str
    all_day: bool = False
    source: str = "gcal"
    #: Where the event is, when the source says. Additive and defaulted, so the
    #: connector path (which does not read it) and every existing consumer are
    #: unaffected; the n8n timeline path fills it from the entry's `sub`.
    location: str | None = None


def _stored_token() -> str | None:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def connection_state() -> str:
    """"present" | "absent" | "unknown" — see :mod:`backend.agent.credentials`.

    An unreadable keyring is not a missing connection: reporting "not
    connected" would send the user back through the whole OAuth consent flow to
    replace a token that is still sitting there.
    """
    from backend.agent.credentials import KEY_ABSENT, KEY_PRESENT, KEY_UNKNOWN

    try:
        return KEY_PRESENT if _stored_token() is not None else KEY_ABSENT
    except Exception:  # noqa: BLE001 - keyring backends raise many types
        return KEY_UNKNOWN


def configured() -> bool:
    """True when a usable OAuth token is stored."""
    from backend.agent.credentials import KEY_PRESENT

    return connection_state() == KEY_PRESENT


def connect(
    credentials_file: Path | None = None, timeout_seconds: float | None = None
) -> None:
    """Run the one-time browser consent flow and store the token (I4).

    ``credentials_file`` defaults to `legacy_credentials_file` when omitted
    (as the CLI does) rather than a fixed module-level path — a mutable
    default evaluated once at import time cannot pick up ``ARGUS_ENV_FILE``,
    which is exactly what the packaged app relies on. Callers that already
    know where the client file lives (the HTTP endpoint) pass it explicitly
    and are unaffected.

    ``timeout_seconds`` bounds the wait for the browser redirect. The CLI
    leaves it None (a person at a terminal can take as long as they like); the
    HTTP endpoint sets it, because an abandoned consent must not pin a worker
    thread for the life of the process.
    """
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials_file = credentials_file or legacy_credentials_file()
    if not credentials_file.is_file():
        raise FileNotFoundError(
            f"{credentials_file} not found — create a Desktop OAuth client in Google Cloud "
            "Console and save its JSON here first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0, timeout_seconds=timeout_seconds)
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, creds.to_json())


def disconnect() -> None:
    """Forget the stored token. Best-effort — already-absent is success."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:  # noqa: BLE001 - already gone, or the keyring is unusable
        pass


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
    """Insert one Argus block. Only ``backend.vault.writer`` may call this (I1)."""
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
    """Events for one local day; [] when unconfigured. ``service`` injectable.

    Raises `ConnectorUnavailable` when a token IS stored but the client
    library can't be imported (a broken build), authentication/refresh fails,
    or the API call itself fails (network outage, a Google outage) — see
    `list_events_safe` for callers that must degrade instead of raise.
    """
    if service is None:
        try:
            service = _service()
        except ImportError as exc:
            raise ConnectorUnavailable(
                f"Google Calendar client library is not installed: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - refresh/auth failures are "unavailable" too
            raise ConnectorUnavailable(f"Google Calendar authentication failed: {exc}") from exc
    if service is None:
        return []
    start = datetime.combine(day, time.min).astimezone()
    end = start + timedelta(days=1)
    try:
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
    except Exception as exc:  # noqa: BLE001 - any API/network failure is "unavailable"
        raise ConnectorUnavailable(f"Google Calendar request failed: {exc}") from exc
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


def list_events_safe(day: date, service=None) -> tuple[list[CalendarEvent], str | None]:
    """`list_events`, but for callers that must degrade rather than fail.

    A calendar widget, the task board, or the planner must not take down the
    whole dashboard because gcal is unreachable or a build is missing the
    client library. Returns ``(events, None)`` on success and
    ``([], message)`` when the connector is unavailable — ``message`` is
    short and safe to show a user directly.
    """
    try:
        return list_events(day, service), None
    except ConnectorUnavailable as exc:
        return [], str(exc)
