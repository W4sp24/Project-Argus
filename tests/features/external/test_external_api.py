"""Auth, abuse resistance, and the write/push surface of ``/api/external/*``.

The privacy boundary is tested separately in ``test_external_privacy.py``.
This file covers the other two things holding the line: the bearer token and
the abuse limits, plus the endpoints themselves.

B4: auth is per n8n instance now (``auth.verify`` is gone). Rather than
monkeypatching a single global check, these tests register one or more real
instances through :mod:`backend.features.automations.store` and seed their
tokens through a fake in-memory keyring (matching
``tests/features/automations/test_automations_api.py``'s pattern), so
``auth.resolve_token`` runs for real against genuine per-instance credentials.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agent.credentials import KeyringUnavailableError, delete_key, store_key
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.external import auth
from backend.features.external.app import create_external_app

TOKEN = "correct-horse-battery-staple"
INSTANCE_ID = "instance-a"


class _FakeKeyring:
    """A minimal in-memory stand-in for the ``keyring`` module."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.values[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.values.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.values.pop((service, ref), None)


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
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fk = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fk)
    return fk


def _register_instance(
    settings: Settings, *, instance_id: str = INSTANCE_ID, name: str = "home"
) -> dict[str, Any]:
    """Append one instance to the registry, bypassing the HTTP endpoint."""
    entry = {
        "id": instance_id,
        "name": name,
        "kind": "REMOTE",
        "base_url": "http://n8n.test",
        "key_ref": store.key_ref_for(name),
    }
    instances = store.load_instances(settings.automations_file)
    instances.append(entry)
    store.save_instances(settings.automations_file, instances)
    return entry


def _seed_token(instance_id: str, token: str) -> None:
    """Store a known token value for ``instance_id`` through the (fake) keyring."""
    store_key(auth.token_ref_for(instance_id), token)


@pytest.fixture()
def instance(settings: Settings, fake_keyring: _FakeKeyring) -> dict[str, Any]:
    """One registered instance whose token is the module-level ``TOKEN``."""
    entry = _register_instance(settings)
    _seed_token(entry["id"], TOKEN)
    return entry


@pytest.fixture()
def client(settings: Settings, clock: _FakeClock, instance: dict[str, Any]) -> TestClient:
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


def test_a_non_ascii_token_is_rejected_not_crashed(instance: dict[str, Any]) -> None:
    """hmac.compare_digest raises TypeError on a str containing non-ASCII.

    Asserted against auth.resolve_token directly rather than through the app
    because httpx refuses to encode a non-ASCII header at all — but a raw
    socket, curl, or any non-Python client will happily send those bytes.
    Reaching resolve_token() with non-ASCII must resolve to no match, never an
    exception that becomes a 500 advertising an unhandled path.
    """
    instances = [instance]
    assert auth.resolve_token("ééé", instances) is None
    assert auth.resolve_token("￿", instances) is None
    assert auth.resolve_token(TOKEN, instances) == instance["id"]


def test_resolve_token_is_none_for_everything_when_no_token_is_stored(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    entry = _register_instance(settings)  # registered, but no token ever issued
    instances = [entry]
    assert auth.resolve_token("anything", instances) is None
    assert auth.resolve_token("", instances) is None
    assert auth.resolve_token(None, instances) is None


def test_resolve_token_is_none_with_zero_instances_registered() -> None:
    """Correct with nothing registered at all: no crash, no match."""
    assert auth.resolve_token("anything", []) is None


def test_resolve_token_survives_an_unreadable_keyring(
    instance: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ref: str) -> str:
        raise KeyringUnavailableError("locked")

    monkeypatch.setattr(auth, "get_key", boom)
    assert auth.resolve_token(TOKEN, [instance]) is None


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
        stored = store.get_widget(conn, "weather", instance_id=INSTANCE_ID)
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
        stored = store.get_widget(conn, "w", instance_id=INSTANCE_ID)
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
        assert store.get_widget(conn, "anything", instance_id=INSTANCE_ID) is None
    finally:
        conn.close()


def test_a_push_for_an_unknown_run_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/external/widget/x?run=nope",
        headers=_auth(),
        json={"widget": "text", "body": "hi"},
    )
    assert response.status_code == 404


# --- B4: per-instance auth and attribution -------------------------------------


def test_two_instances_two_tokens_each_authenticates_and_deleting_one_revokes_only_it(
    settings: Settings, fake_keyring: _FakeKeyring, clock: _FakeClock
) -> None:
    """The whole point of this chunk: revoking one instance's token must not
    silence any other instance's."""
    instance_a = _register_instance(settings, instance_id="a", name="home")
    instance_b = _register_instance(settings, instance_id="b", name="work")
    _seed_token("a", "token-a")
    _seed_token("b", "token-b")

    limiter = auth.RateLimiter(clock=clock)
    client = TestClient(create_external_app(settings, limiter=limiter))

    assert client.get(
        "/api/external/tasks", headers={"Authorization": "Bearer token-a"}
    ).status_code == 200
    assert client.get(
        "/api/external/tasks", headers={"Authorization": "Bearer token-b"}
    ).status_code == 200

    # Delete instance a: its token leaves the keyring, and it leaves the
    # registry — exactly what backend.features.automations.router's
    # delete_instance_by_id does.
    delete_key(auth.token_ref_for(instance_a["id"]))
    remaining = [
        entry
        for entry in store.load_instances(settings.automations_file)
        if entry["id"] != instance_a["id"]
    ]
    store.save_instances(settings.automations_file, remaining)

    assert client.get(
        "/api/external/tasks", headers={"Authorization": "Bearer token-a"}
    ).status_code == 401
    # b's token still works — deleting a must not have touched it.
    assert client.get(
        "/api/external/tasks", headers={"Authorization": "Bearer token-b"}
    ).status_code == 200
    assert instance_b["id"] == "b"  # sanity: b was never removed above


def test_a_push_authenticated_with_bs_token_is_stored_with_bs_instance_id(
    settings: Settings, fake_keyring: _FakeKeyring, clock: _FakeClock
) -> None:
    _register_instance(settings, instance_id="a", name="home")
    _register_instance(settings, instance_id="b", name="work")
    _seed_token("a", "token-a")
    _seed_token("b", "token-b")

    limiter = auth.RateLimiter(clock=clock)
    client = TestClient(create_external_app(settings, limiter=limiter))

    response = client.post(
        "/api/external/widget/inbox",
        headers={"Authorization": "Bearer token-b"},
        json={"widget": "metric", "label": "Inbox", "value": 7},
    )
    assert response.status_code == 200, response.text

    conn = _db(settings)
    try:
        assert store.get_widget(conn, "inbox", instance_id="b") is not None
        assert store.get_widget(conn, "inbox", instance_id="a") is None
    finally:
        conn.close()


# --- the surface is deliberately small ----------------------------------------


@pytest.mark.parametrize("path", ["/api/external/chat", "/api/external/agent", "/api/external/run"])
def test_no_agent_or_action_surface_exists(client: TestClient, path: str) -> None:
    """Their absence is why there is no LLM spend behind a public URL."""
    assert client.post(path, headers=_auth(), json={}).status_code == 404
