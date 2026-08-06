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
) -> dict[str, Any]:
    """Register an instance directly through the store/keyring, bypassing the
    HTTP endpoint — matches test_automations_api.py's helper of the same name.
    Returns the entry it wrote, so a caller can key the per-instance external
    token off its real ``id``."""
    import uuid

    from backend.agent.credentials import store_key

    key_ref = store.key_ref_for(name)
    entry = {
        "id": uuid.uuid4().hex,
        "name": name,
        "kind": "REMOTE",
        "base_url": base_url,
        "key_ref": key_ref,
    }
    instances = store.load_instances(settings.automations_file)
    instances.append(entry)
    store.save_instances(settings.automations_file, instances)
    store_key(key_ref, api_key)
    return entry


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
        # Install applies the `argus` tag after creation, because n8n
        # refuses it in the create body (read-only). Handlers that do not
        # care which tag path is taken just answer generically.
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/google-calendar/install")

    assert response.status_code == 201
    body = response.json()
    assert body["workflow_id"] == "wf-created-1"
    assert body["open_in_n8n"] == "http://n8n.test/workflow/wf-created-1"

    # And the install's own refresh pass cached the new workflow, scoped to
    # the instance it was installed into (B3) rather than the '' sentinel.
    conn = _conn(settings)
    try:
        cached_by_id = {row["id"]: row for row in store.list_workflows(conn)}
    finally:
        conn.close()
    assert "wf-created-1" in cached_by_id
    cached = cached_by_id["wf-created-1"]
    assert cached["name"] == created_name
    assert cached["instance_id"] != ""


def test_install_generates_a_token_when_none_exists_yet(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    from backend.features.external import auth as external_auth

    instance = _seed_instance(settings, base_url="http://n8n.test")
    token_ref = external_auth.token_ref_for(instance["id"])
    assert fake_keyring.values.get(("argus-models", token_ref)) is None

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(200, {"id": "wf-1", "name": "x", "active": False, "tags": []})
        if request.method == "POST" and path == "/api/v1/workflows/wf-1/activate":
            return json_response(200, {"id": "wf-1", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})
        # Install applies the `argus` tag after creation, because n8n
        # refuses it in the create body (read-only). Handlers that do not
        # care which tag path is taken just answer generically.
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/weather/install")

    assert response.status_code == 201
    assert fake_keyring.values.get(("argus-models", token_ref)) is not None


def test_install_bakes_in_the_target_instances_own_token_not_another_instances(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Installing with instance B's bearer credential would make B's workflow
    callbacks authenticate as A instead — mis-attributing every push it ever
    makes. The token baked into the rendered definition must be B's own."""
    from backend.features.external import auth as external_auth

    home = _seed_instance(settings, name="home", base_url="http://home.n8n.test")
    work = _seed_instance(settings, name="work", base_url="http://work.n8n.test")

    # home already has a token from an earlier action — distinct from
    # whatever gets generated for work below.
    home_token = external_auth.generate_token(home["id"])

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            body = json.loads(request.content)
            node = next(n for n in body["nodes"] if n["name"] == "Push Weather Widget")
            headers = node["parameters"]["headerParameters"]["parameters"]
            captured["bearer"] = next(h["value"] for h in headers if h["name"] == "Authorization")
            return json_response(
                200, {"id": "wf-work-1", "name": "x", "active": False, "tags": []}
            )
        if request.method == "POST" and path == "/api/v1/workflows/wf-work-1/activate":
            return json_response(200, {"id": "wf-work-1", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})
        # Install applies the `argus` tag after creation, because n8n
        # refuses it in the create body (read-only). Handlers that do not
        # care which tag path is taken just answer generically.
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{work['id']}/templates/weather/install"
    )
    assert response.status_code == 201, response.text

    work_token = external_auth.get_token(work["id"])
    assert work_token is not None
    assert work_token != home_token
    assert captured["bearer"] == f"Bearer {work_token}"


# --- gallery chips (B8) -----------------------------------------------------
#
# Chips are derived from each bundled definition rather than written by hand
# in `_TEMPLATE_META`, for the same reason `name` is. These tests are the
# thing that makes that worth doing: they compare the chip against the
# definition's own schedule/form nodes, so a template whose interval is
# edited without its card being updated cannot pass.


def _definition_cadence(template_id: str) -> str:
    definition = catalog.load_definition(template_id)
    node = next(n for n in definition["nodes"] if "scheduleTrigger" in n["type"])
    interval = node["parameters"]["rule"]["interval"][0]
    field = interval["field"]
    return f"every {interval[f'{field}Interval']}{field[0]}"


def test_display_template_chips_name_the_renderer_it_pushes() -> None:
    by_id = {t.id: t for t in catalog.list_templates()}
    assert "timeline" in by_id["google-calendar"].chips
    assert "list" in by_id["todoist"].chips
    assert "metric" in by_id["weather"].chips


def test_cadence_chip_matches_the_definitions_own_schedule_node() -> None:
    for template in catalog.list_templates():
        expected = None
        definition = catalog.load_definition(template.id)
        if any("scheduleTrigger" in n["type"] for n in definition["nodes"]):
            expected = _definition_cadence(template.id)
        if expected is not None:
            assert expected in template.chips, f"{template.id} chips={template.chips}"


def test_action_template_chips_count_its_form_fields() -> None:
    by_id = {t.id: t for t in catalog.list_templates()}
    for template_id in ("mobile-capture", "calendar-insert"):
        definition = catalog.load_definition(template_id)
        node = next(n for n in definition["nodes"] if "formTrigger" in n["type"])
        count = len(node["parameters"]["formFields"]["values"])
        assert f"{count} fields" in by_id[template_id].chips


def test_chips_omit_what_a_template_does_not_declare() -> None:
    """A form-only template has no schedule, so it gets no cadence chip —
    filler like "every —" would be worse than a shorter card."""
    by_id = {t.id: t for t in catalog.list_templates()}
    assert not any(c.startswith("every ") for c in by_id["mobile-capture"].chips)


def test_chips_reach_the_templates_route(settings: Settings) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    body = client.get("/api/automations/templates").json()
    by_id = {entry["id"]: entry for entry in body}

    assert by_id["weather"]["chips"] == ["metric", "every 30m"]
    assert by_id["mobile-capture"]["chips"] == ["2 fields"]


# --- install against n8n's real create contract ------------------------------


def test_create_body_omits_tags_and_they_are_applied_afterwards(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Reproduces a real 400 from a live n8n: `request/body/tags is read-only`.

    n8n rejects the whole create when the body carries a server-owned field,
    so a template that ships the `argus` tag inline installs nothing at all.
    Tags cannot simply be dropped either — the tag IS the registration, so a
    workflow installed without it is invisible to Argus, and the install
    would "succeed" while the workflow never appeared.
    """
    _seed_instance(settings)
    seen: dict[str, Any] = {"create_keys": None, "tagged": None, "activated": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            body = json.loads(request.content)
            seen["create_keys"] = sorted(body.keys())
            # Mirror n8n: refuse outright if a read-only field is present.
            if "tags" in body:
                return json_response(400, {"message": "request/body/tags is read-only"})
            return json_response(200, {"id": "wf-new", "name": body.get("name"), "active": False})
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-1", "name": "argus"}]})
        if request.method == "PUT" and path == "/api/v1/workflows/wf-new/tags":
            seen["tagged"] = json.loads(request.content)
            return json_response(200, [{"id": "tag-1", "name": "argus"}])
        if request.method == "POST" and path == "/api/v1/workflows/wf-new/activate":
            seen["activated"] = True
            return json_response(200, {"id": "wf-new", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})
        # Install applies the `argus` tag after creation, because n8n
        # refuses it in the create body (read-only). Handlers that do not
        # care which tag path is taken just answer generically.
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/weather/install")

    assert response.status_code == 201, response.text
    assert "tags" not in (seen["create_keys"] or [])
    # The tag still lands, by the route n8n actually supports.
    assert seen["tagged"] == [{"id": "tag-1"}]
    assert seen["activated"] is True


def test_install_creates_the_argus_tag_when_the_instance_has_none(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """A fresh n8n has no `argus` tag, and tags are assigned by id — so the
    first install has to be able to create it."""
    _seed_instance(settings)
    created_tag: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(200, {"id": "wf-new", "active": False})
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": []})  # nothing defined yet
        if request.method == "POST" and path == "/api/v1/tags":
            created_tag.update(json.loads(request.content))
            return json_response(200, {"id": "tag-new", "name": created_tag.get("name")})
        if request.method == "PUT" and path == "/api/v1/workflows/wf-new/tags":
            assert json.loads(request.content) == [{"id": "tag-new"}]
            return json_response(200, [])
        if request.method == "POST" and path == "/api/v1/workflows/wf-new/activate":
            return json_response(200, {"id": "wf-new", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})
        # Install applies the `argus` tag after creation, because n8n
        # refuses it in the create body (read-only). Handlers that do not
        # care which tag path is taken just answer generically.
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    assert client.post("/api/automations/templates/weather/install").status_code == 201
    assert created_tag == {"name": "argus"}
