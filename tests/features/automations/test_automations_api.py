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
from backend.features.automations import store
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


def test_patch_widget_sets_layout_taken_control(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    conn = _conn(settings)
    try:
        store.upsert_widget(
            conn, "inbox-count", "metric", {"label": "x", "value": 1}, now=fixed_clock
        )
    finally:
        conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    client = app_with(settings, handler)
    response = client.patch("/api/automations/widgets/inbox-count", json={"pinned": True})
    assert response.status_code == 200
    assert response.json()["pinned"] is True

    conn = _conn(settings)
    try:
        assert store.get_pref(conn, "layout_taken_control") == "true"
    finally:
        conn.close()

    assert client.delete("/api/automations/widgets/inbox-count").status_code == 200
    assert client.get("/api/automations/widgets").json() == []
    assert client.delete("/api/automations/widgets/ghost").status_code == 404


# --- wiring ---------------------------------------------------------------


def test_routes_are_mounted_on_the_real_app(tmp_path: Path) -> None:
    from backend.main import create_app

    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault)))

    response = client.get("/api/automations")
    assert response.status_code == 200
    assert response.json()["instance"] is None
