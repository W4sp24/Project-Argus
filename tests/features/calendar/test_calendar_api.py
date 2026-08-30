"""The /api/calendar surface.

Two properties here matter more than the CRUD, because both fail *silently*
if they regress:

* **A feed URL never comes back out of the API.** Google puts the secret in
  the path of its "secret address in iCal format", so a response that echoes
  the URL hands whoever reads it the whole calendar. Asserted against the raw
  response body, not against a parsed field, because the leak this guards
  against is one that adds a field nobody meant to add.

* **A subscribed calendar refuses writes.** An .ics feed is read-only by
  protocol, so accepting an edit and dropping it at the next sync is the bad
  kind of wrong: the event appears, the user trusts it, it vanishes an hour
  later with no error anywhere.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.features.calendar.router import build_calendar_router

SECRET_URL = "https://calendar.google.com/calendar/ical/abc123secret/basic.ics"

FEED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:evt-1
SUMMARY:Lecture
DTSTART:20260901T090000Z
DTEND:20260901T103000Z
END:VEVENT
END:VCALENDAR
"""


class _FakeKeyring:
    """Enough keyring for the router; the real one is absent in CI."""

    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, user: str, value: str) -> None:
        self.saved[(service, user)] = value

    def get_password(self, service: str, user: str) -> str | None:
        return self.saved.get((service, user))

    def delete_password(self, service: str, user: str) -> None:
        self.saved.pop((service, user), None)


@pytest.fixture()
def keyring(monkeypatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    return root


def _client(vault: Path, handler=None) -> TestClient:
    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler or _serve_feed))

    app = FastAPI()
    app.include_router(
        build_calendar_router(
            Settings(_vault_path=vault), client_factory=factory if handler is not False else None
        )
    )
    return TestClient(app)


def _serve_feed(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=FEED, headers={"ETag": '"v1"'})


# --- Local events -----------------------------------------------------------


def test_a_fresh_install_has_a_calendar_to_write_to(vault: Path) -> None:
    """Zero setup is the feature; the default calendar must simply exist."""
    client = _client(vault)
    calendars = client.get("/api/calendar/calendars").json()
    assert [row["id"] for row in calendars] == ["local"]
    assert calendars[0]["kind"] == "local"


def test_create_read_update_delete_an_event(vault: Path) -> None:
    client = _client(vault)
    created = client.post(
        "/api/calendar/events",
        json={"title": "Dentist", "start": "2026-09-01T14:00:00", "end": "2026-09-01T15:00:00"},
    )
    assert created.status_code == 201, created.text
    event = created.json()
    assert event["editable"] is True

    listed = client.get(
        "/api/calendar/events", params={"start": "2026-09-01", "end": "2026-09-02"}
    ).json()
    assert [row["title"] for row in listed] == ["Dentist"]

    patched = client.patch(
        f"/api/calendar/events/{event['id']}", json={"title": "Dentist (moved)"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "Dentist (moved)"

    assert client.delete(f"/api/calendar/events/{event['id']}").status_code == 200
    assert (
        client.get(
            "/api/calendar/events", params={"start": "2026-09-01", "end": "2026-09-02"}
        ).json()
        == []
    )


def test_cancelling_one_occurrence_keeps_the_series(vault: Path) -> None:
    """`scope=one` is an EXDATE, not a delete — next week must still happen."""
    client = _client(vault)
    event = client.post(
        "/api/calendar/events",
        json={
            "title": "Standup",
            "start": "2026-09-01T09:00:00",
            "end": "2026-09-01T09:15:00",
            "rrule": "FREQ=DAILY",
        },
    ).json()

    week = {"start": "2026-09-01", "end": "2026-09-08"}
    before = client.get("/api/calendar/events", params=week).json()
    assert len(before) == 7

    target = before[2]["id"]
    cancelled = client.delete(f"/api/calendar/events/{target}", params={"scope": "one"})
    assert cancelled.status_code == 200

    after = client.get("/api/calendar/events", params=week).json()
    assert len(after) == 6, "one occurrence cancelled, the rest of the series intact"
    assert target not in [row["id"] for row in after]
    assert event["id"].split("::")[0] == target.split("::")[0]


def test_an_end_before_its_start_is_rejected(vault: Path) -> None:
    client = _client(vault)
    response = client.get(
        "/api/calendar/events", params={"start": "2026-09-05", "end": "2026-09-01"}
    )
    assert response.status_code == 422


# --- Subscriptions ----------------------------------------------------------


def test_subscribing_stores_the_url_only_in_the_keyring(vault: Path, keyring) -> None:
    """The property that keeps a secret iCal URL secret."""
    client = _client(vault)
    response = client.post(
        "/api/calendar/subscriptions", json={"name": "Uni timetable", "url": SECRET_URL}
    )
    assert response.status_code == 201, response.text

    assert "abc123secret" not in response.text, "the secret URL came back out of the API"
    assert response.json()["url_display"] == "calendar.google.com/…"

    listed = client.get("/api/calendar/calendars")
    assert "abc123secret" not in listed.text

    # It is genuinely stored, just not served.
    assert SECRET_URL in keyring.saved.values()


def test_the_feed_is_read_and_its_events_appear(vault: Path, keyring) -> None:
    client = _client(vault)
    client.post("/api/calendar/subscriptions", json={"name": "Uni", "url": SECRET_URL})
    events = client.get(
        "/api/calendar/events", params={"start": "2026-09-01", "end": "2026-09-02"}
    ).json()
    assert [row["title"] for row in events] == ["Lecture"]
    assert events[0]["editable"] is False


def test_a_subscribed_calendar_refuses_writes(vault: Path, keyring) -> None:
    """Read-only by protocol, so say so rather than dropping the edit later."""
    client = _client(vault)
    created = client.post(
        "/api/calendar/subscriptions", json={"name": "Uni", "url": SECRET_URL}
    ).json()

    response = client.post(
        "/api/calendar/events",
        json={
            "title": "Nope",
            "start": "2026-09-01T10:00:00",
            "end": "2026-09-01T11:00:00",
            "calendar_id": created["id"],
        },
    )
    assert response.status_code == 422
    assert "read-only" in response.json()["detail"]


def test_an_unreachable_feed_is_refused_before_it_is_saved(vault: Path, keyring) -> None:
    """A saved-but-broken subscription renders as an empty calendar."""

    def _gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    client = _client(vault, handler=_gone)
    response = client.post(
        "/api/calendar/subscriptions", json={"name": "Uni", "url": SECRET_URL}
    )
    assert response.status_code == 422
    assert "404" in response.json()["detail"]
    assert [row["id"] for row in client.get("/api/calendar/calendars").json()] == ["local"]
    assert not keyring.saved, "nothing persisted for a subscription that was refused"


def test_the_local_calendar_cannot_be_unsubscribed(vault: Path) -> None:
    client = _client(vault)
    response = client.delete("/api/calendar/subscriptions/local")
    assert response.status_code == 422


# --- Export -----------------------------------------------------------------


def test_local_events_can_be_exported(vault: Path) -> None:
    """SQLite events are invisible in Obsidian; export is why that is safe."""
    client = _client(vault)
    client.post(
        "/api/calendar/events",
        json={"title": "Gym", "start": "2026-09-01T07:00:00", "end": "2026-09-01T08:00:00"},
    )
    response = client.get("/api/calendar/export.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    body = response.text
    assert "BEGIN:VCALENDAR" in body and "SUMMARY:Gym" in body
