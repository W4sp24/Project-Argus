"""Auth, abuse resistance, and the write/push surface of ``/api/external/*``.

The privacy boundary is tested separately in ``test_external_privacy.py``.
This file covers the other two things holding the line: the bearer token and
the abuse limits, plus the endpoints themselves.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.external import auth
from backend.features.external.app import create_external_app

TOKEN = "correct-horse-battery-staple"


class _FakeClock:
    """A monotonic clock the test advances by hand. No sleeping."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for folder in ("10-Daily", "20-Projects", "99-Private"):
        (root / folder).mkdir(parents=True)
    (root / "20-Projects" / "p.md").write_text("# P\n\n- [ ] a task\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )
    return root


@pytest.fixture()
def settings(vault: Path) -> Settings:
    return Settings(_vault_path=vault)


@pytest.fixture()
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture()
def client(settings: Settings, clock: _FakeClock, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth, "verify", lambda presented: presented == TOKEN)
    limiter = auth.RateLimiter(clock=clock)
    return TestClient(create_external_app(settings, limiter=limiter))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# --- auth ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Token {TOKEN}"},
    ],
    ids=["absent", "empty", "no-value", "wrong-scheme", "wrong-token", "bad-scheme"],
)
def test_every_bad_credential_is_a_401(client: TestClient, headers: dict) -> None:
    assert client.get("/api/external/tasks", headers=headers).status_code == 401


def test_the_401_body_is_identical_for_every_cause(client: TestClient) -> None:
    """A 401 that explains itself tells an attacker which half they got right."""
    bodies = {
        client.get("/api/external/tasks").text,
        client.get("/api/external/tasks", headers={"Authorization": "Bearer x"}).text,
        client.get("/api/external/tasks", headers={"Authorization": "garbage"}).text,
    }
    assert len(bodies) == 1


def test_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    response = client.get(
        "/api/external/tasks", headers={"Authorization": f"bEaReR {TOKEN}"}
    )
    assert response.status_code == 200


def test_a_non_ascii_token_is_rejected_not_crashed(monkeypatch) -> None:
    """hmac.compare_digest raises TypeError on a str containing non-ASCII.

    Asserted at this level rather than through the app because httpx refuses
    to encode a non-ASCII header at all — but a raw socket, curl, or any
    non-Python client will happily send those bytes. Reaching verify() with
    non-ASCII must be a plain False, never an exception that becomes a 500
    advertising an unhandled path.
    """
    monkeypatch.setattr(auth, "get_key", lambda ref: TOKEN)
    assert auth.verify("ééé") is False
    assert auth.verify("\uffff") is False
    assert auth.verify(TOKEN) is True


def test_verify_is_false_for_everything_when_no_token_is_stored(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_key", lambda ref: None)
    assert auth.verify("anything") is False
    assert auth.verify("") is False
    assert auth.verify(None) is False


def test_verify_survives_an_unreadable_keyring(monkeypatch) -> None:
    def boom(ref):
        raise auth.KeyringUnavailableError("locked")

    monkeypatch.setattr(auth, "get_key", boom)
    assert auth.verify("anything") is False


# --- ping ---------------------------------------------------------------------


def test_ping_needs_no_auth_and_returns_204(client: TestClient) -> None:
    response = client.get("/api/external/ping")
    assert response.status_code == 204
    assert response.content == b""


# --- rate limit ---------------------------------------------------------------


def test_the_61st_request_in_a_minute_is_throttled(
    client: TestClient, clock: _FakeClock
) -> None:
    for _ in range(60):
        assert client.get("/api/external/ping").status_code == 204
    assert client.get("/api/external/ping").status_code == 429


def test_the_bucket_refills_over_time(client: TestClient, clock: _FakeClock) -> None:
    for _ in range(60):
        client.get("/api/external/ping")
    assert client.get("/api/external/ping").status_code == 429
    clock.advance(30)  # half a window back
    assert client.get("/api/external/ping").status_code == 204


def test_the_rate_limit_applies_before_auth(client: TestClient) -> None:
    """Unauthenticated floods must be throttled too.

    Checking auth first would leave the token itself brute-forceable at full
    speed, since a rejected request would never touch the bucket.
    """
    for _ in range(60):
        client.get("/api/external/tasks")  # all 401s, all consuming budget
    assert client.get("/api/external/tasks", headers=_auth()).status_code == 429


# --- body cap -----------------------------------------------------------------


def test_an_oversized_body_is_refused_with_413(client: TestClient) -> None:
    huge = "x" * (auth.MAX_BODY_BYTES + 1024)
    response = client.post(
        "/api/external/capture", headers=_auth(), json={"body": huge}
    )
    assert response.status_code == 413


def test_a_normal_body_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/external/capture", headers=_auth(), json={"body": "a small note"}
    )
    assert response.status_code == 200, response.text


# --- writes go through writer.py ----------------------------------------------


def test_capture_writes_into_the_vault(client: TestClient, vault: Path) -> None:
    response = client.post(
        "/api/external/capture",
        headers=_auth(),
        json={"body": "captured from n8n", "title": "Note"},
    )
    assert response.status_code == 200, response.text
    written = (vault / response.json()["path"]).read_text(encoding="utf-8")
    assert "captured from n8n" in written


def test_capture_rejects_an_empty_body(client: TestClient) -> None:
    assert (
        client.post("/api/external/capture", headers=_auth(), json={"body": "  "}).status_code
        == 422
    )


def test_task_lands_as_a_checkbox_with_its_due_date(
    client: TestClient, vault: Path
) -> None:
    response = client.post(
        "/api/external/tasks",
        headers=_auth(),
        json={"text": "call the bank", "due": "2026-09-01", "tags": ["errand"]},
    )
    assert response.status_code == 200, response.text
    written = (vault / response.json()["path"]).read_text(encoding="utf-8")
    assert "- [ ] call the bank" in written
    assert "2026-09-01" in written
    assert "#errand" in written


# --- widget push --------------------------------------------------------------


def _db(settings: Settings):
    conn = connect(settings.db_path)
    init_schema(conn)
    return conn


def test_a_valid_widget_push_is_stored(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/external/widget/weather",
        headers=_auth(),
        json={
            "widget": "metric",
            "title": "WEATHER",
            "label": "Manila",
            "value": "31C",
            "expected_interval_seconds": 1800,
        },
    )
    assert response.status_code == 200, response.text
    conn = _db(settings)
    try:
        stored = store.get_widget(conn, "weather")
    finally:
        conn.close()
    assert stored is not None
    assert stored["kind"] == "metric"
    assert stored["expected_interval_seconds"] == 1800


def test_an_unknown_kind_is_422_and_names_the_valid_ones(client: TestClient) -> None:
    response = client.post(
        "/api/external/widget/x", headers=_auth(), json={"widget": "sparkline"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "metric" in detail and "timeline" in detail


def test_a_malformed_push_keeps_the_previous_good_payload(
    client: TestClient, settings: Settings
) -> None:
    """A broken push must never replace good data with nothing.

    Yesterday's numbers on a panel beat an error where the numbers were.
    """
    good = {"widget": "metric", "label": "Manila", "value": "31C"}
    assert (
        client.post("/api/external/widget/w", headers=_auth(), json=good).status_code == 200
    )

    broken = {"widget": "metric"}  # no label, no value
    assert (
        client.post("/api/external/widget/w", headers=_auth(), json=broken).status_code == 422
    )

    conn = _db(settings)
    try:
        stored = store.get_widget(conn, "w")
    finally:
        conn.close()
    assert stored["payload"]["value"] == "31C"


def test_a_run_scoped_push_targets_the_run_not_the_dashboard(
    client: TestClient, settings: Settings
) -> None:
    conn = _db(settings)
    try:
        store.record_run_started(conn, "run-1", "wf-1")
    finally:
        conn.close()

    response = client.post(
        "/api/external/widget/anything?run=run-1",
        headers=_auth(),
        json={"widget": "text", "body": "the answer"},
    )
    assert response.status_code == 200, response.text

    conn = _db(settings)
    try:
        runs = store.list_runs(conn)
        assert runs[0]["status"] == "ok"
        assert runs[0]["mode"] == "widget"
        # ...and no dashboard widget was created for it
        assert store.get_widget(conn, "anything") is None
    finally:
        conn.close()


def test_a_push_for_an_unknown_run_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/external/widget/x?run=nope",
        headers=_auth(),
        json={"widget": "text", "body": "hi"},
    )
    assert response.status_code == 404


# --- the surface is deliberately small ----------------------------------------


@pytest.mark.parametrize("path", ["/api/external/chat", "/api/external/agent", "/api/external/run"])
def test_no_agent_or_action_surface_exists(client: TestClient, path: str) -> None:
    """Their absence is why there is no LLM spend behind a public URL."""
    assert client.post(path, headers=_auth(), json={}).status_code == 404
