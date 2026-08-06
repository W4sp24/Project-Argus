"""Tests for /api/automations — register, discover, run.

Every case drives a bare FastAPI app mounting ``build_automations_router``
with an injected ``client_factory`` (an ``N8nClient`` over
``httpx.MockTransport``, matching ``tests/features/automations/test_n8n_client.py``)
and a fake keyring module (matching ``tests/agent/test_credentials.py``'s
pattern of ``monkeypatch.setitem(sys.modules, "keyring", ...)``). No real
network, no real OS keyring, no ``time.sleep`` — the clock is injected too.
"""

from __future__ import annotations

import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.credentials import CredentialError
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import catalog, store
from backend.features.automations.n8n_client import N8nClient
from backend.features.automations.router import build_automations_router

FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


# --- fake keyring -------------------------------------------------------


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


class _BrokenKeyring:
    """A keyring whose writes always fail — for the rollback test."""

    def set_password(self, service: str, ref: str, value: str) -> None:
        raise RuntimeError("the credential store is locked")

    def get_password(self, service: str, ref: str) -> str | None:
        return None

    def delete_password(self, service: str, ref: str) -> None:
        pass


@pytest.fixture()
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fk = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fk)
    return fk


@pytest.fixture()
def broken_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", _BrokenKeyring())


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault)


# --- client / transport helpers ------------------------------------------

Handler = Any


def factory_with(handler: Handler):
    """A ``client_factory`` whose N8nClient always uses ``handler`` for transport."""

    def _factory(instance: dict[str, Any], api_key: str) -> N8nClient:
        return N8nClient(
            base_url=instance["base_url"], api_key=api_key, transport=httpx.MockTransport(handler)
        )

    return _factory


def app_with(settings: Settings, handler: Handler) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_automations_router(settings, client_factory=factory_with(handler), now=fixed_clock)
    )
    return TestClient(app)


def json_response(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _conn(settings: Settings):
    conn = connect(settings.db_path)
    init_schema(conn)
    return conn


def _seed_instance(
    settings: Settings,
    *,
    name: str = "home",
    base_url: str = "http://n8n.test",
    api_key: str = "n8n-secret",
    kind: str = "REMOTE",
) -> dict[str, Any]:
    """Register an instance directly through the store/keyring, bypassing the
    HTTP endpoint — for tests that care about something downstream of
    registration, not registration itself. Appends to whatever is already
    registered (via ``store.save_instances``'s list shape) rather than
    overwriting it, so this can be called more than once to seed several
    instances. Returns the entry it wrote."""
    from backend.agent.credentials import store_key

    key_ref = store.key_ref_for(name)
    entry = {
        "id": uuid.uuid4().hex,
        "name": name,
        "kind": kind,
        "base_url": base_url,
        "key_ref": key_ref,
    }
    instances = store.load_instances(settings.automations_file)
    instances.append(entry)
    store.save_instances(settings.automations_file, instances)
    store_key(key_ref, api_key)
    return entry


WORKFLOW_DEF: dict[str, Any] = {
    "id": "wf1",
    "name": "Send Report",
    "active": True,
    "tags": [{"id": "t1", "name": "argus"}],
    "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "send-report"}}],
}

ASYNC_WORKFLOW_DEF: dict[str, Any] = {
    "id": "wf-async",
    "name": "Long Job",
    "active": True,
    "tags": [{"name": "argus"}, {"name": "argus:async"}],
    "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "long-job"}}],
}


def _seed_workflow(settings: Settings, definition: dict[str, Any] = WORKFLOW_DEF) -> None:
    conn = _conn(settings)
    try:
        store.upsert_workflow(
            conn,
            str(definition["id"]),
            name=definition["name"],
            tags=["argus"],
            schema_json=definition,
            active=bool(definition.get("active", False)),
            now=fixed_clock,
        )
    finally:
        conn.close()


# --- instance registration (plural, POST /automations/instances) -----------


def test_register_probe_failure_persists_nothing(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(401, {"message": "invalid API key"})

    client = app_with(settings, handler)
    response = client.post(
        "/api/automations/instances",
        json={"name": "home", "base_url": "http://n8n.test", "api_key": "bad"},
    )

    assert response.status_code == 422
    assert store.load_instances(settings.automations_file) == []
    assert fake_keyring.values == {}


def test_register_success_persists_and_never_echoes_the_key(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.post(
        "/api/automations/instances",
        json={"name": "home", "base_url": "http://n8n.test", "api_key": "s3cret-key"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["has_key"] is True
    assert body["key_state"] == "present"
    assert body["kind"] == "REMOTE"  # the default
    assert isinstance(body["id"], str) and body["id"]
    assert "s3cret-key" not in response.text

    on_disk = settings.automations_file.read_text(encoding="utf-8")
    assert "s3cret-key" not in on_disk

    instances = store.load_instances(settings.automations_file)
    assert len(instances) == 1
    assert instances[0]["name"] == "home"
    assert instances[0]["id"] == body["id"]
    assert fake_keyring.values[("argus-models", "n8n:home")] == "s3cret-key"


def test_register_duplicate_name_is_rejected_but_a_second_distinct_instance_succeeds(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """409 now means duplicate NAME, not "an instance already exists" —
    registering a second, distinctly-named instance is the whole point of
    this chunk."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    home = {"name": "home", "base_url": "http://n8n.test", "api_key": "key"}
    first = client.post("/api/automations/instances", json=home)
    assert first.status_code == 201

    # Same name again: still 409.
    assert client.post("/api/automations/instances", json=home).status_code == 409

    # A second, distinctly-named instance: succeeds.
    work = {"name": "work", "base_url": "http://work.n8n.test", "api_key": "key2"}
    second = client.post("/api/automations/instances", json=work)
    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]

    instances = store.load_instances(settings.automations_file)
    assert {entry["name"] for entry in instances} == {"home", "work"}


def test_register_rejects_invalid_kind(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.post(
        "/api/automations/instances",
        json={
            "name": "home",
            "base_url": "http://n8n.test",
            "api_key": "key",
            "kind": "CLOUD",
        },
    )
    assert response.status_code == 422


def test_register_keyring_failure_rolls_back_the_registry_row(
    settings: Settings, broken_keyring: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.post(
        "/api/automations/instances",
        json={"name": "home", "base_url": "http://n8n.test", "api_key": "key"},
    )

    assert response.status_code == 500
    assert store.load_instances(settings.automations_file) == [], "the rollback must have run"


def test_register_keyring_failure_rolls_back_only_the_new_row(
    settings: Settings, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash storing the *second* instance's key must not disturb the first."""
    _seed_instance(settings, name="home")

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    def _broken_store_key(ref: str, value: str) -> None:
        raise CredentialError("the credential store is locked")

    monkeypatch.setattr("backend.features.automations.router.store_key", _broken_store_key)

    client = app_with(settings, handler)
    response = client.post(
        "/api/automations/instances",
        json={"name": "work", "base_url": "http://work.n8n.test", "api_key": "key2"},
    )

    assert response.status_code == 500
    instances = store.load_instances(settings.automations_file)
    assert [entry["name"] for entry in instances] == ["home"]
    assert fake_keyring.values[("argus-models", "n8n:home")] == "n8n-secret"


# --- instance registry: singular compat shim ---------------------------------


def test_register_compat_singular_matches_old_behaviour_for_one_instance(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    body = {"name": "home", "base_url": "http://n8n.test", "api_key": "key"}
    first = client.post("/api/automations/instance", json=body)
    assert first.status_code == 201
    assert first.json()["has_key"] is True

    # A second registration via the singular route is rejected even under a
    # different name — "an instance already exists", not "duplicate name".
    other = {"name": "other", "base_url": "http://other.n8n.test", "api_key": "key2"}
    assert client.post("/api/automations/instance", json=other).status_code == 409


def test_delete_removes_keyring_before_registry_and_clears_cache(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """The singular compat shim: byte-identical to before multi-instance
    support for a single registered instance."""
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.delete("/api/automations/instance")

    assert response.status_code == 200
    assert store.load_instances(settings.automations_file) == []
    assert fake_keyring.values == {}

    conn = _conn(settings)
    try:
        assert store.list_workflows(conn) == []
    finally:
        conn.close()

    assert client.delete("/api/automations/instance").status_code == 404


# --- instance registry: plural routes (list / delete by id) -----------------


def test_list_instances_returns_both(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.get("/api/automations/instances")

    assert response.status_code == 200
    body = response.json()
    assert {row["name"] for row in body} == {"home", "work"}
    assert all(row["connected"] is True for row in body)


def test_list_instances_probe_failure_on_one_does_not_fail_the_whole_call(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "work.n8n.test":
            raise httpx.ConnectError("connection refused", request=request)
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.get("/api/automations/instances")

    assert response.status_code == 200
    body = response.json()
    by_name = {row["name"]: row for row in body}
    assert by_name["home"]["connected"] is True
    assert by_name["work"]["connected"] is False


def test_delete_by_id_removes_only_that_instance_leaving_the_other_intact(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.delete(f"/api/automations/instances/{home['id']}")

    assert response.status_code == 200
    instances = store.load_instances(settings.automations_file)
    assert [entry["id"] for entry in instances] == [work["id"]]

    # home's keyring secret is gone...
    assert fake_keyring.values.get(("argus-models", home["key_ref"])) is None
    # ...but work's is untouched.
    assert fake_keyring.values.get(("argus-models", work["key_ref"])) == "n8n-secret"


def test_delete_by_id_also_revokes_only_that_instances_external_token(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """B4: deleting an instance must delete its inbound external token from
    the keyring alongside its n8n API key, and must not touch any other
    instance's token — this is the whole point of per-instance auth."""
    from backend.features.external import auth as external_auth

    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")
    home_token = external_auth.generate_token(home["id"])
    work_token = external_auth.generate_token(work["id"])

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.delete(f"/api/automations/instances/{home['id']}")
    assert response.status_code == 200

    # home's token is gone — it no longer resolves against either instance...
    remaining = store.load_instances(settings.automations_file)
    assert external_auth.resolve_token(home_token, remaining) is None
    home_ref = external_auth.token_ref_for(home["id"])
    assert fake_keyring.values.get(("argus-models", home_ref)) is None
    # ...but work's token is untouched.
    assert external_auth.resolve_token(work_token, remaining) == work["id"]


def test_delete_by_id_unknown_id_404s(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    _seed_instance(settings)
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.delete("/api/automations/instances/does-not-exist")
    assert response.status_code == 404


# --- discovery -----------------------------------------------------------


def test_get_automations_returns_200_with_connected_false_when_n8n_is_unreachable(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = app_with(settings, handler)
    response = client.get("/api/automations")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["detail"]
    # The dashboard never goes blank because n8n is down: the cached card is
    # still there.
    assert [w["id"] for w in body["workflows"]] == ["wf1"]
    assert body["instance"]["has_key"] is True


def test_get_automations_returns_instance_null_but_populated_instances_with_two_registered(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.get("/api/automations")

    assert response.status_code == 200
    body = response.json()
    assert body["instance"] is None
    assert {row["name"] for row in body["instances"]} == {"home", "work"}


def test_refresh_drops_a_workflow_that_lost_the_tag(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    both = [WORKFLOW_DEF, {**WORKFLOW_DEF, "id": "wf2", "name": "Second"}]
    only_first = [WORKFLOW_DEF]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        payload = both if calls["n"] == 1 else only_first
        return json_response(200, {"data": payload})

    client = app_with(settings, handler)
    first = client.post("/api/automations/refresh")
    assert first.status_code == 200
    assert first.json() == {"ok": True, "count": 2, "dropped": 0}

    second = client.post("/api/automations/refresh")
    assert second.status_code == 200
    assert second.json() == {"ok": True, "count": 1, "dropped": 1}

    conn = _conn(settings)
    try:
        assert [w["id"] for w in store.list_workflows(conn)] == ["wf1"]
    finally:
        conn.close()


# --- run dispatch ---------------------------------------------------------


def test_run_ack_mode(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "ack"
    assert body["message"] is None

    runs = client.get("/api/automations/runs").json()
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    assert runs[0]["mode"] == "ack"


def test_run_status_ok_mode(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": True, "message": "sent 3 emails"})

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "status"
    assert body["message"] == "sent 3 emails"

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "ok" and runs[0]["message"] == "sent 3 emails"


def test_run_status_failed_mode(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": False, "message": "SMTP rejected"})

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    body = response.json()
    assert body["status"] == "failed"
    assert body["mode"] == "status"
    assert body["message"] == "SMTP rejected"

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "failed"


def test_run_widget_mode_validates_and_persists(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200, {"widget": "metric", "slug": "inbox-count", "label": "Inbox", "value": 42}
        )

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "widget"
    assert body["payload"] == {"label": "Inbox", "value": 42}

    widgets = client.get("/api/automations/widgets").json()
    assert widgets[0]["slug"] == "inbox-count"
    assert widgets[0]["payload"] == {"label": "Inbox", "value": 42}


def test_run_invalid_widget_payload_fails_run_and_keeps_previous_good_payload(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    good = {"widget": "metric", "slug": "inbox-count", "label": "Inbox", "value": 42}
    bad = {"widget": "metric", "slug": "inbox-count", "label": "Inbox"}  # missing 'value'
    responses = [good, bad]

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, responses.pop(0))

    client = app_with(settings, handler)
    first = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert first.json()["status"] == "ok"

    second = client.post("/api/automations/wf1/run", json={"payload": {}})
    body = second.json()
    assert body["status"] == "failed"
    assert body["mode"] == "widget"
    assert "value" in body["message"]

    widgets = client.get("/api/automations/widgets").json()
    assert widgets[0]["payload"] == {"label": "Inbox", "value": 42}, (
        "a broken push must never overwrite a good stored payload"
    )

    runs = client.get("/api/automations/runs").json()
    # Both runs share the injected fixed clock's timestamp, so only the
    # multiset of statuses is guaranteed, not their relative order.
    assert sorted(r["status"] for r in runs) == ["failed", "ok"]


def test_run_n8n_500_records_failed_with_execution_id_and_link(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(500, {"message": "workflow crashed", "executionId": "exec-500"})

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    body = response.json()
    assert body["status"] == "failed"
    assert body["execution_id"] == "exec-500"
    assert body["execution_url"] == "http://n8n.test/workflow/executions/exec-500"

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["execution_id"] == "exec-500"
    assert runs[0]["status"] == "failed"


def test_run_timeout_records_status_timeout_and_never_calls_stop(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        raise httpx.ConnectTimeout("timed out", request=request)

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    body = response.json()
    assert body["status"] == "timeout"

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "timeout"
    # n8n owns the workflow on a client-side timeout — Argus never asks it to
    # stop.
    assert not any("stop" in path for path in seen_paths)


def test_run_async_workflow_returns_a_run_id_without_waiting(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings, ASYNC_WORKFLOW_DEF)
    entered = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        return json_response(200, {"ok": True})

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf-async/run", json={"payload": {}})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "running"
    assert body["mode"] is None
    assert body["run_id"]

    # The run is left "running": the real outcome arrives later via the
    # external ?run={id} push, not via this fire's own HTTP response.
    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "running"


def test_double_fire_is_rejected_with_exactly_one_run_row(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(timeout=5), "the second request never arrived"
        return httpx.Response(200, content=b"")

    client = app_with(settings, handler)
    results: dict[str, httpx.Response] = {}

    def fire_first() -> None:
        results["first"] = client.post("/api/automations/wf1/run", json={"payload": {}})

    thread = threading.Thread(target=fire_first)
    thread.start()
    assert entered.wait(timeout=5), "the first request's handler never ran"

    second = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert second.status_code == 409

    release.set()
    thread.join(timeout=5)

    assert results["first"].status_code == 200
    runs = client.get("/api/automations/runs").json()
    assert len(runs) == 1


# --- widgets ---------------------------------------------------------------


def _seed_widget(settings: Settings, slug: str = "inbox-count") -> None:
    conn = _conn(settings)
    try:
        store.upsert_widget(conn, slug, "metric", {"label": "x", "value": 1}, now=fixed_clock)
    finally:
        conn.close()


def test_patch_widget_locks_only_that_widgets_layout(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Taking control is per widget, not global. The old behaviour set one
    `layout_taken_control` pref, which meant nudging one card froze the whole
    dashboard and every automation installed afterwards arrived needing manual
    placement — the opposite of auto-place."""
    _seed_widget(settings)
    _seed_widget(settings, "untouched")

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.patch("/api/automations/widgets/inbox-count", json={"pinned": True})
    assert response.status_code == 200
    assert response.json()["pinned"] is True
    assert response.json()["layout_locked"] is True

    widgets = {w["slug"]: w for w in client.get("/api/automations/widgets").json()}
    assert widgets["untouched"]["layout_locked"] is False

    conn = _conn(settings)
    try:
        assert store.get_pref(conn, "layout_taken_control") is None
    finally:
        conn.close()

    assert client.delete("/api/automations/widgets/inbox-count").status_code == 200
    remaining = [w["slug"] for w in client.get("/api/automations/widgets").json()]
    assert remaining == ["untouched"]
    assert client.delete("/api/automations/widgets/ghost").status_code == 404


def test_patch_widget_persists_the_grid_footprint(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_widget(settings)
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    response = client.patch(
        "/api/automations/widgets/inbox-count", json={"grid_cols": 2, "grid_rows": 3}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["grid_cols"], response.json()["grid_rows"]) == (2, 3)
    assert response.json()["layout_locked"] is True

    reread = client.get("/api/automations/widgets").json()[0]
    assert (reread["grid_cols"], reread["grid_rows"]) == (2, 3)


@pytest.mark.parametrize("bad", [0, 5, -1])
def test_patch_widget_rejects_a_grid_footprint_off_the_board(
    settings: Settings, fake_keyring: _FakeKeyring, bad: int
) -> None:
    """422, not 500: an out-of-range span is a malformed request, and the
    fresh-DB CHECK cannot be retrofitted onto an ALTERed column, so the
    range check has to hold here."""
    _seed_widget(settings)
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    response = client.patch("/api/automations/widgets/inbox-count", json={"grid_cols": bad})
    assert response.status_code == 422, response.text


def test_legacy_global_layout_pref_is_folded_onto_existing_widgets(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Someone who took control under the old global model had genuinely
    arranged everything they could see, so every existing widget inherits the
    lock — and the pref is consumed, so anything installed later still
    auto-places."""
    _seed_widget(settings, "old-one")
    _seed_widget(settings, "old-two")
    conn = _conn(settings)
    try:
        store.set_pref(conn, "layout_taken_control", "true")
    finally:
        conn.close()

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    widgets = client.get("/api/automations/widgets").json()
    assert all(w["layout_locked"] is True for w in widgets)

    conn = _conn(settings)
    try:
        assert store.get_pref(conn, "layout_taken_control") is None
        store.upsert_widget(
            conn, "installed-later", "metric", {"label": "x", "value": 1}, now=fixed_clock
        )
    finally:
        conn.close()

    later = {w["slug"]: w for w in client.get("/api/automations/widgets").json()}
    assert later["installed-later"]["layout_locked"] is False


# --- B3: instance-scoped refresh/run/widgets/discover -----------------------


def test_refresh_by_id_prunes_only_this_instances_stale_cache(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Two instances that happen to cache the same n8n-numbered workflow: a
    refresh that drops home's tag must never touch work's cached row for the
    same id."""
    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    conn = _conn(settings)
    try:
        store.upsert_workflow(
            conn, "shared-id", name="Home WF (about to lose the tag)", tags=["argus"],
            schema_json=None, active=True, instance_id=home["id"], now=fixed_clock,
        )
        store.upsert_workflow(
            conn, "shared-id", name="Work WF", tags=["argus"], schema_json=None,
            active=True, instance_id=work["id"], now=fixed_clock,
        )
    finally:
        conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "home.n8n.test":
            return json_response(200, {"data": []})  # nothing tagged argus anymore
        raise AssertionError("only home should be refreshed by this call")

    client = app_with(settings, handler)
    response = client.post(f"/api/automations/instances/{home['id']}/refresh")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "count": 0, "dropped": 1}

    conn = _conn(settings)
    try:
        assert store.get_workflow(conn, "shared-id", instance_id=home["id"]) is None
        assert store.get_workflow(conn, "shared-id", instance_id=work["id"]) is not None
    finally:
        conn.close()


def test_run_compat_shim_409s_on_cross_instance_id_collision_but_works_without_one(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    conn = _conn(settings)
    try:
        # "5" is cached on both instances -- a genuine collision.
        store.upsert_workflow(
            conn, "5", name="Home WF", tags=["argus"], schema_json=WORKFLOW_DEF,
            active=True, instance_id=home["id"], now=fixed_clock,
        )
        store.upsert_workflow(
            conn, "5", name="Work WF", tags=["argus"], schema_json=WORKFLOW_DEF,
            active=True, instance_id=work["id"], now=fixed_clock,
        )
        # "unique-1" only lives on home -- no collision.
        store.upsert_workflow(
            conn, "unique-1", name="Send Report", tags=["argus"], schema_json=WORKFLOW_DEF,
            active=True, instance_id=home["id"], now=fixed_clock,
        )
    finally:
        conn.close()

    client = app_with(settings, lambda request: httpx.Response(200, content=b""))

    collision = client.post("/api/automations/5/run", json={"payload": {}})
    assert collision.status_code == 409
    assert "instances" in collision.json()["detail"]

    ok = client.post("/api/automations/unique-1/run", json={"payload": {}})
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"


def test_run_scoped_route_runs_on_exactly_the_named_instance(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    conn = _conn(settings)
    try:
        store.upsert_workflow(
            conn, "5", name="Home WF", tags=["argus"], schema_json=WORKFLOW_DEF,
            active=True, instance_id=home["id"], now=fixed_clock,
        )
        store.upsert_workflow(
            conn, "5", name="Work WF", tags=["argus"], schema_json=WORKFLOW_DEF,
            active=True, instance_id=work["id"], now=fixed_clock,
        )
    finally:
        conn.close()

    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(200, content=b"")

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{home['id']}/workflows/5/run", json={"payload": {}}
    )

    assert response.status_code == 200
    assert seen_hosts == ["home.n8n.test"]

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["instance_id"] == home["id"]


def test_patch_and_delete_widget_ambiguous_slug_409s_but_scoped_instance_id_works(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    conn = _conn(settings)
    try:
        store.upsert_widget(conn, "w", "metric", {"value": 1}, instance_id="a", now=fixed_clock)
        store.upsert_widget(conn, "w", "metric", {"value": 2}, instance_id="b", now=fixed_clock)
    finally:
        conn.close()

    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    ambiguous_patch = client.patch("/api/automations/widgets/w", json={"pinned": True})
    assert ambiguous_patch.status_code == 409

    scoped_patch = client.patch(
        "/api/automations/widgets/w", json={"pinned": True}, params={"instance_id": "a"}
    )
    assert scoped_patch.status_code == 200
    assert scoped_patch.json()["instance_id"] == "a"
    assert scoped_patch.json()["pinned"] is True

    # "b" untouched by the scoped patch above.
    conn = _conn(settings)
    try:
        assert store.get_widget(conn, "w", instance_id="b")["pinned"] is False
    finally:
        conn.close()

    ambiguous_delete = client.delete("/api/automations/widgets/w")
    assert ambiguous_delete.status_code == 409

    scoped_delete = client.delete("/api/automations/widgets/w", params={"instance_id": "b"})
    assert scoped_delete.status_code == 200

    conn = _conn(settings)
    try:
        assert store.get_widget(conn, "w", instance_id="b") is None
        assert store.get_widget(conn, "w", instance_id="a") is not None  # untouched
    finally:
        conn.close()


def test_discover_returns_tagged_workflows_without_persisting_anything(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "data": [
                    {
                        "id": "wf1",
                        "name": "Send Report",
                        "active": True,
                        "tags": [{"name": "argus"}],
                        "nodes": [
                            {"type": "n8n-nodes-base.webhook", "parameters": {"path": "x"}}
                        ],
                    },
                    {
                        "id": "wf2",
                        "name": "Cron Push",
                        "active": False,
                        "tags": [{"name": "argus"}],
                        "nodes": [{"type": "n8n-nodes-base.cron", "parameters": {}}],
                    },
                ]
            },
        )

    client = app_with(settings, handler)
    response = client.post(
        "/api/automations/instances/discover",
        json={"base_url": "http://n8n.test", "api_key": "key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert {w["id"] for w in body} == {"wf1", "wf2"}
    by_id = {w["id"]: w for w in body}
    assert by_id["wf1"]["kind"] == "action"
    assert by_id["wf2"]["kind"] == "display"
    assert all(w["tagged"] is True for w in body)

    # Not registered, and nothing was persisted by discovering it.
    assert not settings.automations_file.exists()
    assert not settings.db_path.exists()


# --- B5: activity events ----------------------------------------------------


def test_run_ack_success_writes_a_run_event(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    client = app_with(settings, lambda request: httpx.Response(200, content=b""))
    response = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert response.status_code == 200

    events = client.get("/api/automations/events").json()
    run_events = [e for e in events if e["tag"] == "RUN"]
    assert len(run_events) == 1
    assert run_events[0]["subject"] == "Send Report"
    assert "s" in run_events[0]["text"]  # a duration was included


def test_run_widget_success_writes_a_run_event_with_execution_id(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {
                "widget": "metric", "slug": "inbox-count", "label": "Inbox", "value": 42,
                "executionId": "exec-1",
            },
        )

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert response.status_code == 200

    events = client.get("/api/automations/events").json()
    run_events = [e for e in events if e["tag"] == "RUN"]
    assert len(run_events) == 1
    assert "exec-1" in run_events[0]["text"]


def test_run_status_failed_writes_a_fail_event_with_the_run_message(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": False, "message": "SMTP rejected"})

    client = app_with(settings, handler)
    response = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert response.status_code == 200

    events = client.get("/api/automations/events").json()
    fail_events = [e for e in events if e["tag"] == "FAIL"]
    assert len(fail_events) == 1
    assert fail_events[0]["text"] == "SMTP rejected"
    assert fail_events[0]["subject"] == "Send Report"
    assert not any(e["tag"] == "RUN" for e in events)


def test_run_n8n_500_writes_a_fail_event(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(500, {"message": "workflow crashed", "executionId": "exec-500"})

    client = app_with(settings, handler)
    client.post("/api/automations/wf1/run", json={"payload": {}})

    events = client.get("/api/automations/events").json()
    fail_events = [e for e in events if e["tag"] == "FAIL"]
    assert len(fail_events) == 1
    assert "workflow crashed" in fail_events[0]["text"]


def test_run_timeout_writes_a_fail_event(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = app_with(settings, handler)
    client.post("/api/automations/wf1/run", json={"payload": {}})

    events = client.get("/api/automations/events").json()
    assert any(e["tag"] == "FAIL" for e in events)


def test_install_writes_an_install_event(
    tmp_path: Path, fake_keyring: _FakeKeyring
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(_vault_path=vault, external_base_url="http://tunnel.example.test")
    _seed_instance(settings)
    created_name = catalog.load_definition("google-calendar")["name"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(
                200,
                {"id": "wf-created-1", "name": created_name, "active": False, "tags": ["argus"]},
            )
        if request.method == "POST" and path == "/api/v1/workflows/wf-created-1/activate":
            return json_response(
                200, {"id": "wf-created-1", "name": created_name, "active": True}
            )
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(
                200,
                {
                    "data": [
                        {
                            "id": "wf-created-1", "name": created_name, "active": True,
                            "tags": [{"name": "argus"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/google-calendar/install")
    assert response.status_code == 201

    events = client.get("/api/automations/events").json()
    install_events = [e for e in events if e["tag"] == "INSTALL"]
    assert len(install_events) == 1
    assert install_events[0]["subject"] == created_name
    assert "wf-created-1" in install_events[0]["text"]


def test_a_failing_record_event_does_not_fail_a_successful_run(
    settings: Settings, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cleanest proof: make record_event itself raise and confirm the
    run it was describing still completes and reports success."""
    _seed_instance(settings)
    _seed_workflow(settings)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("db is on fire")

    monkeypatch.setattr("backend.features.automations.router.store.record_event", boom)

    client = app_with(settings, lambda request: httpx.Response(200, content=b""))
    response = client.post("/api/automations/wf1/run", json={"payload": {}})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    runs = client.get("/api/automations/runs").json()
    assert runs[0]["status"] == "ok"


def test_events_route_filters_by_tag_case_insensitively_and_by_instance_and_respects_limit(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings, name="home")
    work = _seed_instance(settings, name="work")

    conn = _conn(settings)
    try:
        store.record_event(conn, tag="RUN", text="r1", instance_id=home["id"], now=fixed_clock)
        store.record_event(conn, tag="PUSH", text="p1", instance_id=home["id"], now=fixed_clock)
        store.record_event(conn, tag="RUN", text="r2", instance_id=work["id"], now=fixed_clock)
    finally:
        conn.close()

    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    all_events = client.get("/api/automations/events").json()
    assert len(all_events) == 3

    # Lowercase, as the UI's filter names are -- normalised in the route.
    by_tag = client.get("/api/automations/events", params={"tag": "run"}).json()
    assert {e["text"] for e in by_tag} == {"r1", "r2"}

    by_instance = client.get(
        "/api/automations/events", params={"instance_id": home["id"]}
    ).json()
    assert {e["text"] for e in by_instance} == {"r1", "p1"}

    limited = client.get("/api/automations/events", params={"limit": 1}).json()
    assert len(limited) == 1

    bad_tag = client.get("/api/automations/events", params={"tag": "bogus"})
    assert bad_tag.status_code == 422


# --- B7: cancel --------------------------------------------------------------


def test_cancel_unknown_run_404s(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.post("/api/automations/runs/nope/cancel")
    assert response.status_code == 404


def test_cancel_already_finished_run_refuses_with_409(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)
    _seed_workflow(settings)
    client = app_with(settings, lambda request: httpx.Response(200, content=b""))
    run = client.post("/api/automations/wf1/run", json={"payload": {}}).json()
    assert run["status"] == "ok"

    response = client.post(f"/api/automations/runs/{run['run_id']}/cancel")
    assert response.status_code == 409


def test_cancel_with_known_execution_id_calls_stop_execution_and_marks_failed(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings)
    _seed_workflow(settings)

    conn = _conn(settings)
    try:
        store.record_run_started(
            conn, "run-1", "wf1", workflow_name="Send Report",
            instance_id=home["id"], now=fixed_clock,
        )
        conn.execute(
            "UPDATE automation_runs SET execution_id = ? WHERE id = ?", ("exec-9", "run-1")
        )
        conn.commit()
    finally:
        conn.close()

    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/v1/executions/exec-9/stop":
            return httpx.Response(200, content=b"")
        raise AssertionError(f"unexpected request: {request.url.path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/runs/run-1/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["execution_id"] == "exec-9"
    assert "cancelled" in body["message"].lower()
    assert "/api/v1/executions/exec-9/stop" in seen_paths

    events = client.get("/api/automations/events").json()
    fail_events = [e for e in events if e["tag"] == "FAIL"]
    assert any("cancelled" in e["text"].lower() for e in fail_events)


def test_cancel_with_no_execution_id_never_calls_n8n_but_still_cancels_locally(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    home = _seed_instance(settings)
    _seed_workflow(settings)

    conn = _conn(settings)
    try:
        store.record_run_started(
            conn, "run-2", "wf1", workflow_name="Send Report",
            instance_id=home["id"], now=fixed_clock,
        )
    finally:
        conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no execution id -- n8n must never be called")

    client = app_with(settings, handler)
    response = client.post("/api/automations/runs/run-2/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["execution_id"] is None
    # Honest about what actually happened: n8n was never asked to stop
    # anything, because there was no execution id to hand it.
    assert "n8n" in body["message"].lower()
    assert "cancelled" in body["message"].lower()


def test_cancel_frees_the_inflight_lock_so_the_workflow_can_run_again(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """The real in-flight window: a cancel that arrives while the original
    fire() is still blocked must free the lock immediately -- a cancelled
    run that leaves its own lock held would make the workflow permanently
    unrunnable until restart."""
    _seed_instance(settings)
    _seed_workflow(settings)
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert release.wait(timeout=5), "the first request never got released"
        return httpx.Response(200, content=b"")

    client = app_with(settings, handler)
    results: dict[str, httpx.Response] = {}

    def fire_first() -> None:
        results["first"] = client.post("/api/automations/wf1/run", json={"payload": {}})

    thread = threading.Thread(target=fire_first)
    thread.start()
    assert entered.wait(timeout=5), "the first request's handler never ran"

    # Genuinely in flight: the run row is "running", the lock is held, and
    # no execution_id is known yet -- fire() hasn't returned.
    runs = client.get("/api/automations/runs").json()
    assert len(runs) == 1
    run_id = runs[0]["id"]
    assert runs[0]["status"] == "running"

    cancel = client.post(f"/api/automations/runs/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "failed"

    # The lock is free again immediately: a fresh run does not 409.
    again = client.post("/api/automations/wf1/run", json={"payload": {}})
    assert again.status_code == 200, again.text

    release.set()
    thread.join(timeout=5)
    assert results["first"].status_code == 200


# --- wiring ---------------------------------------------------------------


def test_routes_are_mounted_on_the_real_app(tmp_path: Path) -> None:
    from backend.main import create_app

    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault)))

    response = client.get("/api/automations")
    assert response.status_code == 200
    assert response.json()["instance"] is None


def test_reset_layout_hands_every_widget_back_to_auto_place(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Every layout write implicitly locks the widget it touches, which is
    right — nobody drags a card by accident — but a mode with no way out is a
    trap. This is the way out, and it is all-or-nothing on purpose."""
    _seed_widget(settings, "one")
    _seed_widget(settings, "two")
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    client.patch("/api/automations/widgets/one", json={"grid_cols": 3, "grid_rows": 2})
    locked = {w["slug"]: w for w in client.get("/api/automations/widgets").json()}
    assert locked["one"]["layout_locked"] is True
    assert locked["two"]["layout_locked"] is False

    response = client.post("/api/automations/widgets/layout/reset")
    assert response.status_code == 200, response.text
    # Only the one actually under control is reported as released.
    assert response.json()["count"] == 1

    after = {w["slug"]: w for w in client.get("/api/automations/widgets").json()}
    assert all(w["layout_locked"] is False for w in after.values())
    # Sizes go back to the renderers' own defaults rather than freezing
    # whatever span was last dragged to.
    assert (after["one"]["grid_cols"], after["one"]["grid_rows"]) == (1, 1)


def test_reset_layout_route_is_not_shadowed_by_the_slug_route(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """FastAPI matches in declaration order, so `layout` would be read as a
    widget slug if this route were registered after PATCH/DELETE /{slug}."""
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    assert client.post("/api/automations/widgets/layout/reset").status_code == 200


def test_widget_mode_run_names_the_widget_it_wrote(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """A widget-mode run's payload carries only the kind-specific fields —
    ValidatedWidget strips slug and kind. Without naming them on the response
    the caller knows a widget was pushed but not which one, so it cannot
    offer to pin it. The palette's PIN TO DASHBOARD depends on this."""
    instance = _seed_instance(settings)
    _seed_workflow(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "webhook" in request.url.path:
            return json_response(
                200,
                {"widget": "metric", "slug": "doi-lookup", "label": "Cited by", "value": 412},
            )
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{instance['id']}/workflows/wf1/run", json={"payload": {}}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "widget"
    assert body["widget_slug"] == "doi-lookup"
    assert body["widget_kind"] == "metric"
    assert body["instance_id"] == instance["id"]

    # And the named widget is really there to be pinned.
    slugs = [w["slug"] for w in client.get("/api/automations/widgets").json()]
    assert "doi-lookup" in slugs


class _UnreadableKeyring:
    """A keyring whose reads always fail — the `keyring.backends.fail.Keyring`
    state a machine with no usable credential store is actually in."""

    def set_password(self, service: str, ref: str, value: str) -> None:
        pass

    def get_password(self, service: str, ref: str) -> str | None:
        raise RuntimeError("No recommended backend was available")

    def delete_password(self, service: str, ref: str) -> None:
        pass


def test_listing_instances_survives_an_unreadable_keyring(
    settings: Settings, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken credential store must degrade one row, not erase the feature.

    get_key raises rather than returning None when the keyring itself is
    unreadable — deliberately, so "no key stored" and "cannot tell" are not
    confused. But letting that escape the reachability probe 500s the whole
    instance list, and the frontend reads a failed fetch as *no instances
    registered*: the instance grid, the filter and the dashboard's ambient
    readout all vanish while the instances are still registered. A machine
    with an unreadable keyring is a state real users reach — the CI e2e job
    installs no keyring backend on purpose for exactly this reason.
    """
    _seed_instance(settings, name="home")
    _seed_instance(settings, name="work", base_url="http://n8n2.test")

    # Swap in the broken keyring only now, so seeding could still store keys.
    monkeypatch.setitem(sys.modules, "keyring", _UnreadableKeyring())

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.get("/api/automations/instances")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["name"] for entry in body] == ["home", "work"]
    # Degraded, and honest about why — not silently absent.
    assert all(entry["connected"] is False for entry in body)
