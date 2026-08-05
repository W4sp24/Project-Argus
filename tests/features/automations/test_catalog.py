"""Tests for the shipped workflow template gallery: `catalog.py` (pure JSON
handling) and the two `/automations/templates` routes appended to
`router.py`.

Fixtures are owned in this file — there is no shared conftest providing
`vault`/`client` (see `tests/test_app.py:14-33`), and no shared harness for
`build_automations_router` either, so the `settings`/`fake_keyring`/`app_with`
trio below mirrors `tests/features/automations/test_automations_api.py`'s
pattern: a bare FastAPI app mounting the router with an injected
`client_factory` (an `N8nClient` over `httpx.MockTransport`) and a fake
`keyring` module monkeypatched into `sys.modules`. No real network, no real
OS keyring.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import catalog, store
from backend.features.automations.n8n_client import N8nClient
from backend.features.automations.router import build_automations_router
from backend.features.automations.schema import parse_workflow

FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


# --- fake keyring (mirrors test_automations_api.py) -------------------------


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


@pytest.fixture()
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fk = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fk)
    return fk


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault, external_base_url="http://tunnel.example.test")


@pytest.fixture()
def settings_no_base_url(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault, external_base_url="")


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
) -> None:
    """Register an instance directly through the store/keyring, bypassing the
    HTTP endpoint — matches test_automations_api.py's helper of the same name."""
    from backend.agent.credentials import store_key

    key_ref = store.key_ref_for(name)
    entry = {"name": name, "base_url": base_url, "key_ref": key_ref}
    store.save_instance(settings.automations_file, entry)
    store_key(key_ref, api_key)


def _seed_cached_workflow(settings: Settings, *, workflow_id: str, name: str) -> None:
    conn = _conn(settings)
    try:
        store.upsert_workflow(
            conn,
            workflow_id,
            name=name,
            tags=["argus"],
            schema_json={"id": workflow_id, "name": name, "tags": ["argus"]},
            active=True,
            now=fixed_clock,
        )
    finally:
        conn.close()


# --- template-shape helpers --------------------------------------------------

_TRIGGER_TYPES = {"n8n-nodes-base.formTrigger", "n8n-nodes-base.webhook"}

#: Every template whose trigger is a Form Trigger or Webhook, and the
#: respond mode it must declare — the silent-failure guard from catalog.py's
#: module docstring, checked per template by name so a new template can't
#: quietly omit it and still pass.
_EXPECTED_RESPOND_MODE = {
    "mobile-capture": "lastNode",
    "calendar-insert": "lastNode",
}


def _find_trigger_node(definition: dict[str, Any]) -> dict[str, Any] | None:
    for node in definition.get("nodes", []):
        if isinstance(node, dict) and node.get("type") in _TRIGGER_TYPES:
            return node
    return None


def _tag_names(definition: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tag in definition.get("tags", []):
        if isinstance(tag, dict) and tag.get("name"):
            names.add(str(tag["name"]))
        elif isinstance(tag, str):
            names.add(tag)
    return names


ALL_TEMPLATE_IDS = [t.id for t in catalog.list_templates()]


# --- A: every bundled template is valid, tagged JSON -------------------------


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_template_is_valid_tagged_workflow_json(template_id: str) -> None:
    definition = catalog.load_definition(template_id)

    assert isinstance(definition.get("name"), str) and definition["name"]
    nodes = definition.get("nodes")
    assert isinstance(nodes, list) and len(nodes) > 0
    connections = definition.get("connections")
    assert isinstance(connections, dict)
    assert "argus" in _tag_names(definition), f"{template_id} must carry the argus tag"


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_template_trigger_sets_explicit_respond_mode(template_id: str) -> None:
    """The silent-failure guard: a Form Trigger/Webhook without an explicit
    respond mode would ack 200 before the workflow has done anything."""
    definition = catalog.load_definition(template_id)
    trigger = _find_trigger_node(definition)

    expected = _EXPECTED_RESPOND_MODE.get(template_id)
    if expected is None:
        # This template's trigger isn't a Form Trigger/Webhook (e.g. a
        # Schedule Trigger) — the "Immediately" ack problem doesn't apply,
        # since there is no inbound HTTP caller waiting on a response.
        assert trigger is None, (
            f"{template_id} has a form/webhook trigger but no expected respond "
            "mode was declared for it in this test's _EXPECTED_RESPOND_MODE map"
        )
        return

    assert trigger is not None, f"{template_id} was expected to have a form/webhook trigger"
    assert trigger["parameters"].get("responseMode") == expected, (
        f"{template_id}'s trigger must set responseMode={expected!r} explicitly — "
        "n8n's default (Immediately) would ack success before the workflow runs"
    )


def test_calendar_insert_is_present_action_and_replaces_gcal_write_path() -> None:
    templates = {t.id: t for t in catalog.list_templates()}
    assert "calendar-insert" in templates
    template = templates["calendar-insert"]
    assert template.kind == "action"
    assert template.replaces == "backend/connectors/gcal.py"
    assert "Google Calendar" in template.requires


# --- B: form/webhook templates parse cleanly through schema.parse_workflow --


def test_mobile_capture_parses_as_a_form_with_expected_fields() -> None:
    definition = catalog.load_definition("mobile-capture")
    parsed = parse_workflow(definition)

    assert parsed.kind == "form"
    labels = [f.label for f in parsed.fields]
    assert "Note" in labels
    assert "Tags" in labels
    note_field = next(f for f in parsed.fields if f.label == "Note")
    assert note_field.required is True
    assert note_field.type == "text"


def test_calendar_insert_parses_as_a_form_with_expected_fields() -> None:
    definition = catalog.load_definition("calendar-insert")
    parsed = parse_workflow(definition)

    assert parsed.kind == "form"
    labels = {f.label for f in parsed.fields}
    assert labels == {"Title", "Start", "End"}
    assert all(f.required for f in parsed.fields)


@pytest.mark.parametrize("template_id", ["google-calendar", "todoist", "weather"])
def test_display_templates_have_no_runnable_trigger(template_id: str) -> None:
    """Schedule-triggered templates aren't something Argus fires from a card."""
    definition = catalog.load_definition(template_id)
    parsed = parse_workflow(definition)
    assert parsed.kind == "none"


# --- C: render_definition substitution ---------------------------------------


def test_render_definition_substitutes_both_placeholders_everywhere_nested() -> None:
    rendered = catalog.render_definition(
        "google-calendar",
        callback_url="http://tunnel.example.test:8787",
        token="tok_abc123",
    )
    text = json.dumps(rendered)
    assert "{{ARGUS_URL}}" not in text
    assert "{{ARGUS_TOKEN}}" not in text

    push_node = next(n for n in rendered["nodes"] if n["name"] == "Push Timeline Widget")
    # The URL placeholder, nested inside parameters.url.
    assert push_node["parameters"]["url"] == (
        "http://tunnel.example.test:8787/api/external/widget/calendar"
    )
    # The token placeholder, nested inside a list of header-parameter dicts.
    headers = push_node["parameters"]["headerParameters"]["parameters"]
    auth_header = next(h["value"] for h in headers if h["name"] == "Authorization")
    assert auth_header == "Bearer tok_abc123"


def test_render_definition_raises_when_a_placeholder_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_definition = {
        "name": "Broken",
        "nodes": [
            {
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {
                    "url": "{{ARGUS_URL}}",
                    # Not a placeholder render_definition knows how to fill —
                    # simulates a template author adding a new marker without
                    # updating catalog.py's substitution list.
                    "extra": "{{ARGUS_SOMETHING_NEW}}",
                },
            }
        ],
        "connections": {},
    }
    monkeypatch.setattr(catalog, "load_definition", lambda template_id: broken_definition)

    with pytest.raises(catalog.TemplateRenderError):
        catalog.render_definition("broken", callback_url="http://x.test", token="t")


def test_render_definition_round_trips_a_token_with_json_significant_characters() -> None:
    tricky_token = 'weird"token\\with/slashes\nand\tcontrol chars'
    rendered = catalog.render_definition(
        "google-calendar", callback_url="http://tunnel.example.test", token=tricky_token
    )
    push_node = next(n for n in rendered["nodes"] if n["name"] == "Push Timeline Widget")
    headers = push_node["parameters"]["headerParameters"]["parameters"]
    auth_header = next(h["value"] for h in headers if h["name"] == "Authorization")
    assert auth_header == f"Bearer {tricky_token}"

    # And the whole document must still be valid, re-parseable JSON — the
    # quotes/backslashes in the token must not have broken the structure.
    assert json.loads(json.dumps(rendered)) == rendered


def test_render_definition_unknown_template_raises() -> None:
    with pytest.raises(catalog.UnknownTemplate):
        catalog.render_definition("does-not-exist", callback_url="http://x", token="t")


def test_load_definition_unknown_template_raises() -> None:
    with pytest.raises(catalog.UnknownTemplate):
        catalog.load_definition("does-not-exist")


# --- D: GET /automations/templates -------------------------------------------


def test_list_templates_route_returns_the_full_catalog(settings: Settings) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.get("/api/automations/templates")

    assert response.status_code == 200
    body = response.json()
    ids = {entry["id"] for entry in body}
    assert ids == set(ALL_TEMPLATE_IDS)
    assert all(entry["installed"] is False for entry in body)


def test_list_templates_route_marks_installed_by_cached_name(settings: Settings) -> None:
    google_calendar_def = catalog.load_definition("google-calendar")
    _seed_cached_workflow(settings, workflow_id="wf-1", name=google_calendar_def["name"])

    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.get("/api/automations/templates")

    assert response.status_code == 200
    body = {entry["id"]: entry for entry in response.json()}
    assert body["google-calendar"]["installed"] is True
    assert body["todoist"]["installed"] is False


# --- E: POST /automations/templates/{id}/install -----------------------------


def test_install_without_a_configured_callback_url_is_409(
    settings_no_base_url: Settings, fake_keyring: _FakeKeyring
) -> None:
    client = app_with(settings_no_base_url, lambda request: json_response(200, {"data": []}))
    response = client.post("/api/automations/templates/google-calendar/install")
    assert response.status_code == 409


def test_install_unknown_template_is_404(settings: Settings, fake_keyring: _FakeKeyring) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    response = client.post("/api/automations/templates/does-not-exist/install")
    assert response.status_code == 404


def test_install_posts_to_n8n_and_returns_workflow_id_and_open_in_n8n_url(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings, base_url="http://n8n.test")
    created_name = catalog.load_definition("google-calendar")["name"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            body = json.loads(request.content)
            # The rendered definition must have made it into the create call
            # with both placeholders substituted, not the raw template.
            assert "{{ARGUS_URL}}" not in json.dumps(body)
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
                            "id": "wf-created-1",
                            "name": created_name,
                            "active": True,
                            "tags": [{"name": "argus"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/google-calendar/install")

    assert response.status_code == 201
    body = response.json()
    assert body["workflow_id"] == "wf-created-1"
    assert body["open_in_n8n"] == "http://n8n.test/workflow/wf-created-1"

    # And the install's own refresh pass cached the new workflow.
    conn = _conn(settings)
    try:
        cached = store.get_workflow(conn, "wf-created-1")
    finally:
        conn.close()
    assert cached is not None
    assert cached["name"] == created_name


def test_install_generates_a_token_when_none_exists_yet(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings, base_url="http://n8n.test")
    assert fake_keyring.values.get(("argus-models", "external:token")) is None

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(200, {"id": "wf-1", "name": "x", "active": False, "tags": []})
        if request.method == "POST" and path == "/api/v1/workflows/wf-1/activate":
            return json_response(200, {"id": "wf-1", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/weather/install")

    assert response.status_code == 201
    assert fake_keyring.values.get(("argus-models", "external:token")) is not None
