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


# --- GET /automations/actions: reaching a workflow from a native panel --------
#
# The inverse of `widget_slug`. A widget lets a workflow *feed* a panel; an
# action slug lets a panel *reach* a workflow — without which an installed
# action template is unreachable from anywhere but the command palette, which
# is the state `calendar-insert` shipped in.


def _seed_action_workflow(
    settings: Settings,
    *,
    workflow_id: str,
    name: str,
    instance_id: str = "",
    active: bool = True,
    fields: list[str] | None = None,
) -> None:
    """Cache a workflow whose definition carries a real Form Trigger, so the
    route's `parse_workflow` has fields to report."""
    definition: dict[str, Any] = {"id": workflow_id, "name": name, "tags": ["argus"]}
    if fields is not None:
        definition["nodes"] = [
            {
                "id": "trigger",
                "type": "n8n-nodes-base.formTrigger",
                "webhookId": workflow_id,
                "parameters": {
                    "path": workflow_id,
                    "responseMode": "lastNode",
                    "formFields": {
                        "values": [
                            {"fieldLabel": label, "fieldType": "text", "requiredField": True}
                            for label in fields
                        ]
                    },
                },
            }
        ]
    conn = _conn(settings)
    try:
        store.upsert_workflow(
            conn,
            workflow_id,
            name=name,
            tags=["argus"],
            schema_json=definition,
            active=active,
            instance_id=instance_id,
            now=fixed_clock,
        )
    finally:
        conn.close()


def _actions_by_slug(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/automations/actions")
    assert response.status_code == 200
    return {a["action_slug"]: a for a in response.json()}


def test_actions_are_empty_when_nothing_is_installed(settings: Settings, fake_keyring) -> None:
    client = app_with(settings, lambda request: json_response(200, {"data": []}))
    assert client.get("/api/automations/actions").json() == []


def test_an_installed_action_template_resolves_by_slug(settings: Settings, fake_keyring) -> None:
    instance = _seed_instance(settings)
    _seed_action_workflow(
        settings,
        workflow_id="wf-1",
        name="Argus: Add Todoist Task",
        instance_id=instance["id"],
        fields=["Task", "Due", "Priority"],
    )
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    action = _actions_by_slug(client)["task.create"]

    assert action["template_id"] == "todoist-insert"
    assert action["workflow_id"] == "wf-1"
    assert action["instance_id"] == instance["id"]
    assert action["instance_name"] == "home"
    assert action["active"] is True
    # The payload keys a panel must send. n8n has no machine name separate from
    # the visible label, so these *are* the labels — reported rather than
    # assumed, since renaming a field in n8n renames the key.
    assert [f["name"] for f in action["fields"]] == ["Task", "Due", "Priority"]


def test_a_display_template_never_resolves_as_an_action(settings: Settings, fake_keyring) -> None:
    """`todoist` and `todoist-insert` are both Todoist templates; only one of
    them is something a panel can fire."""
    _seed_action_workflow(
        settings, workflow_id="wf-1", name="Argus: Todoist → Task List Widget", fields=[]
    )
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    assert client.get("/api/automations/actions").json() == []


def test_an_inactive_action_is_reported_not_hidden(settings: Settings, fake_keyring) -> None:
    """Inactive almost always means its credential has not been granted in n8n.
    Filtering it out would leave the panel with no button and no explanation;
    reporting it lets the UI say why."""
    _seed_action_workflow(
        settings, workflow_id="wf-1", name="Argus: Add Calendar Event", active=False, fields=[]
    )
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    assert _actions_by_slug(client)["calendar.create"]["active"] is False


def test_an_active_copy_wins_a_cross_instance_name_collision(
    settings: Settings, fake_keyring
) -> None:
    """Unlike the run route, a collision here is not a 409. This endpoint
    answers "can I offer this button?", and failing the request would remove
    the affordance with no way for the user to see why — so the best candidate
    wins instead."""
    first = _seed_instance(settings, name="home")
    second = _seed_instance(settings, name="work", base_url="http://work.test")
    _seed_action_workflow(
        settings,
        workflow_id="wf-inactive",
        name="Argus: Add Todoist Task",
        instance_id=first["id"],
        active=False,
        fields=[],
    )
    _seed_action_workflow(
        settings,
        workflow_id="wf-active",
        name="Argus: Add Todoist Task",
        instance_id=second["id"],
        active=True,
        fields=[],
    )
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    action = _actions_by_slug(client)["task.create"]

    assert action["workflow_id"] == "wf-active"
    assert action["instance_id"] == second["id"]


def test_an_action_with_no_parseable_trigger_still_resolves(
    settings: Settings, fake_keyring
) -> None:
    """A cached definition without nodes (older cache rows carry only the stub)
    must still yield the workflow — with no fields — rather than 500."""
    _seed_cached_workflow(settings, workflow_id="wf-1", name="Argus: Complete Todoist Task")
    client = app_with(settings, lambda request: json_response(200, {"data": []}))

    action = _actions_by_slug(client)["task.complete"]

    assert action["workflow_id"] == "wf-1"
    assert action["fields"] == []


# --- template-shape helpers --------------------------------------------------

_TRIGGER_TYPES = {"n8n-nodes-base.formTrigger", "n8n-nodes-base.webhook"}

#: Every template whose trigger is a Form Trigger or Webhook, and the
#: respond mode it must declare — the silent-failure guard from catalog.py's
#: module docstring, checked per template by name so a new template can't
#: quietly omit it and still pass.
_EXPECTED_RESPOND_MODE = {
    "mobile-capture": "lastNode",
    "calendar-insert": "lastNode",
    "todoist-insert": "lastNode",
    # A Webhook trigger cannot use "lastNode" — n8n spells the equivalent as
    # "responseNode", answered by an explicit Respond to Webhook node (asserted
    # separately below). Same guarantee, different node type.
    "todoist-complete": "responseNode",
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


#: Action templates that deliberately expose no capability to a native panel.
#: Listed explicitly rather than allowed by omission: a missing `action_slug`
#: is not an error anywhere — `GET /automations/actions` simply resolves
#: nothing — so the failure mode is a template that installs cleanly and is
#: then reachable from nowhere but the command palette, which is the state
#: `calendar-insert` shipped in. Requiring the choice to be written down is
#: what makes forgetting it visible.
_PANEL_LESS_ACTIONS = {
    # A form the user opens on their phone. Nothing in the app fires it, so
    # there is no panel that would ask for it by slug.
    "mobile-capture",
}


def test_every_action_template_declares_an_action_slug_or_opts_out() -> None:
    for template in catalog.list_templates():
        if template.kind != "action":
            assert template.action_slug is None, (
                f"{template.id} is a display template but declares an action_slug"
            )
        elif template.id in _PANEL_LESS_ACTIONS:
            assert template.action_slug is None, (
                f"{template.id} is listed as panel-less but declares an action_slug"
            )
        else:
            assert template.action_slug, (
                f"{template.id} is an action template but declares no action_slug, so "
                "no native panel can find it — add one, or add it to _PANEL_LESS_ACTIONS"
            )


def test_action_slugs_are_unique_across_templates() -> None:
    """Two templates claiming one slug would make which workflow a panel fires
    depend on catalog ordering."""
    slugs = [t.action_slug for t in catalog.list_templates() if t.action_slug]
    assert len(slugs) == len(set(slugs)), f"duplicate action slugs: {slugs}"


def test_todoist_write_templates_are_present_and_require_todoist() -> None:
    templates = {t.id: t for t in catalog.list_templates()}
    for template_id, slug in (
        ("todoist-insert", "task.create"),
        ("todoist-complete", "task.complete"),
    ):
        assert template_id in templates, f"{template_id} missing from the gallery"
        template = templates[template_id]
        assert template.kind == "action"
        assert template.action_slug == slug
        assert "Todoist" in template.requires
        assert template.widget_slug is None


def test_todoist_complete_answers_the_run_with_an_explicit_respond_node() -> None:
    """`responseMode: responseNode` names a node that must actually exist — n8n
    otherwise holds the request open until it times out, and Argus records the
    run as a 30s timeout rather than the success it was.

    The body matters too: `_dispatch_result` reads a returned `{"ok": ...}` as
    `mode="status"`, which is what turns a fired webhook into a *reported*
    outcome instead of a bare ack.
    """
    definition = catalog.load_definition("todoist-complete")
    respond = [
        node
        for node in definition["nodes"]
        if node.get("type") == "n8n-nodes-base.respondToWebhook"
    ]
    assert respond, "responseMode=responseNode with no Respond to Webhook node hangs the run"
    assert '"ok"' in str(respond[0]["parameters"].get("responseBody", ""))


def test_todoist_templates_speak_argus_priority_vocabulary() -> None:
    """Todoist encodes p1 as `4`; Argus's TaskItem/widget contract spells it
    `highest`. The translation lives in the template expression because that is
    the only place the *source system's* encoding is known — pushing a raw `4`
    would fail `_validate_list`'s priority check at the door."""
    body = str(_push_nodes(catalog.load_definition("todoist"))[0]["parameters"]["jsonBody"])
    assert "highest" in body and "4:" in body.replace(" ", "").replace("'", "")


def test_todoist_display_template_pushes_the_fields_tasks_due_reads() -> None:
    """`sources._tasks_from_list` maps this payload onto TaskItem, and the
    agenda drops an external task with no `due`. A push of text/sub alone
    therefore renders TASKS.DUE *empty* while the badge reads VIA N8N — which
    is exactly what shipped."""
    body = str(_push_nodes(catalog.load_definition("todoist"))[0]["parameters"]["jsonBody"])
    for field in ('"id"', '"due"', '"priority"', '"tags"', '"href"'):
        assert field in body, f"the todoist push omits {field}"


def test_calendar_template_pushes_end_and_all_day() -> None:
    """Without `end` every event is zero-length: `durationLabel` renders "" and
    `insights._event_hours` counts the whole day as 0 hours of meetings."""
    body = str(_push_nodes(catalog.load_definition("google-calendar"))[0]["parameters"]["jsonBody"])
    assert '"end"' in body and "end.dateTime" in body and "end.date" in body
    assert '"all_day"' in body


# --- A2: the template could actually run ------------------------------------
#
# Everything above checks that a template is well-formed *as a document*. None
# of it checks the things that decide whether n8n can execute it, and all five
# bundled templates once passed this file green while being unable to run at
# all. The checks below are the structural half of that gap — each one is a
# real failure seen against a live n8n, expressed as something readable off the
# bundled JSON:
#
#   * a Form Trigger with no `path` -> "Missing or invalid required
#     parameters: path" at activation.
#   * a `jsonBody` expression returning a bare object -> n8n coerces it and
#     posts the literal string "[object Object]", which fails as invalid JSON.
#   * `$json` in an aggregating push -> `$json` is the *current item*, not the
#     item list, so `.map`/`.items` on it is undefined or not-a-function.
#   * a push node without `executeOnce` -> fires once per upstream item, so a
#     10-event calendar pushes the same widget ten times.
#
# What none of this proves is that a template *runs*; only a live n8n can say
# that. It proves a template cannot ship in a state already known to be broken.


def _push_nodes(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node that POSTs a widget payload back to Argus."""
    return [
        node
        for node in definition.get("nodes", [])
        if isinstance(node, dict)
        and "/api/external/widget/" in str(node.get("parameters", {}).get("url", ""))
    ]


#: Minimum `typeVersion` per vendor node, with the reason it is a floor rather
#: than a preference. This is its own check because pinning a node too low has
#: now broken two templates in the same *silent* way twice: n8n keeps the old
#: version's behaviour alive for backwards compatibility, so an outdated
#: typeVersion does not error — it quietly selects a code path that talks to a
#: deprecated vendor endpoint, or drops parameters the template depends on.
#: Both failures reached a user before anything here noticed.
_MIN_TYPE_VERSIONS: dict[str, tuple[float, str]] = {
    "n8n-nodes-base.googleCalendar": (
        1.3,
        "below 1.3 the node ignores top-level timeMin/timeMax and has no "
        "options.recurringEventHandling, so the time window is never applied",
    ),
    "n8n-nodes-base.todoist": (
        2.2,
        "below 2.2 the node calls Todoist's /sync/v9 and /rest/v2, which now "
        "answer 410 Gone; 2.2 is where n8n switched to /api/v1",
    ),
}


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_vendor_nodes_are_not_pinned_below_their_working_version(template_id: str) -> None:
    definition = catalog.load_definition(template_id)
    for node in definition.get("nodes", []):
        floor = _MIN_TYPE_VERSIONS.get(str(node.get("type")))
        if floor is None:
            continue
        minimum, why = floor
        assert float(node["typeVersion"]) >= minimum, (
            f"{template_id}'s {node.get('name')!r} pins typeVersion "
            f"{node['typeVersion']}, below {minimum}: {why}"
        )


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_form_trigger_declares_a_path_matching_its_webhook_id(template_id: str) -> None:
    """n8n requires `path` on a Form Trigger, and Argus builds the form URL as
    ``/form/{webhookId}`` (n8n_client.py:147) — so the two must agree or the
    workflow activates and Argus still links to a 404."""
    definition = catalog.load_definition(template_id)
    trigger = _find_trigger_node(definition)
    if trigger is None:
        return

    path = trigger.get("parameters", {}).get("path")
    assert path, (
        f"{template_id}'s form trigger declares no `path` — n8n refuses to "
        "activate it with 'Missing or invalid required parameters: path'"
    )
    assert path == trigger.get("webhookId"), (
        f"{template_id}'s form `path` ({path!r}) must equal its webhookId "
        f"({trigger.get('webhookId')!r}) — Argus builds the form URL from the "
        "webhookId, so a mismatch links the RUN button at a 404"
    )


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_push_body_is_stringified_and_never_a_bare_object(template_id: str) -> None:
    definition = catalog.load_definition(template_id)
    for node in _push_nodes(definition):
        body = str(node["parameters"].get("jsonBody", ""))
        assert body.startswith("={{"), (
            f"{template_id}'s {node.get('name')!r} jsonBody must be an n8n "
            "expression (leading '=')"
        )
        assert "JSON.stringify(" in body, (
            f"{template_id}'s {node.get('name')!r} returns a bare object from "
            "jsonBody — n8n coerces that to the literal '[object Object]' and "
            "the push fails as invalid JSON. Wrap it in JSON.stringify()."
        )


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_push_nodes_aggregate_over_all_items_and_fire_once(template_id: str) -> None:
    definition = catalog.load_definition(template_id)
    for node in _push_nodes(definition):
        body = str(node["parameters"].get("jsonBody", ""))
        # A push that maps over a collection is aggregating upstream items, and
        # `$json` cannot supply them — it is one item. `$input.all()` is the
        # only thing that can, and such a node must also fire exactly once.
        if ".map(" not in body:
            continue
        assert "$input.all()" in body, (
            f"{template_id}'s {node.get('name')!r} maps over items but reads "
            "$json — $json is the *current item*, not the item list. Use "
            "$input.all()."
        )
        assert node.get("executeOnce") is True, (
            f"{template_id}'s {node.get('name')!r} aggregates every upstream "
            "item but has no executeOnce, so it fires once per item and pushes "
            "the same payload N times"
        )


def test_google_calendar_query_expands_recurring_events() -> None:
    """Without this, Google returns a recurring series' *master* record, whose
    `start` is the date the series began — so a standup created a year ago
    renders as a year-old entry on a widget titled "Upcoming Events".

    The typeVersion floor these parameters depend on is enforced separately,
    for every vendor node at once — see `_MIN_TYPE_VERSIONS`.
    """
    definition = catalog.load_definition("google-calendar")
    node = next(n for n in definition["nodes"] if n["type"] == "n8n-nodes-base.googleCalendar")

    assert node["parameters"].get("options", {}).get("recurringEventHandling") == "expand"
    assert node["parameters"].get("timeMin")
    assert node["parameters"].get("timeMax")


def test_google_calendar_reads_a_string_start_not_the_start_object() -> None:
    """Google returns `start` as an object — `{dateTime, timeZone}` for a timed
    event, `{date}` for an all-day one — but the timeline validator requires
    `entries[].at` to be a string (schema._validate_timeline)."""
    definition = catalog.load_definition("google-calendar")
    body = str(_push_nodes(definition)[0]["parameters"]["jsonBody"])

    assert "start.dateTime" in body and "start.date" in body, (
        "the calendar push must read start.dateTime || start.date — passing "
        "the bare `start` object fails validation, since `at` must be a string"
    )


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
    # n8n accepted the activation here, so the install is fully done — the
    # contrast case (a refused activation) is the test below.
    assert body["active"] is True
    assert body["activation_error"] is None

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


# --- activation is best-effort, never fatal ----------------------------------
#
# n8n refuses to activate a workflow whose nodes have unconfigured credentials.
# That is the *normal* state of every template with a non-empty `requires` at
# the moment it is installed — the credential is granted afterwards, in n8n.
# Treating it as an install failure made 3 of the 5 shipped templates
# (google-calendar, todoist, calendar-insert) impossible to install at all,
# *and* stranded a real workflow in n8n on every attempt.

#: n8n's actual refusal, verbatim from the bug report.
_CREDENTIAL_REFUSAL = (
    "Cannot publish workflow: 1 node have configuration issues: "
    'Node "Get Upcoming Events": - Credential not configured: googleCalendarOAuth2Api'
)


def test_install_survives_n8n_refusing_to_activate_an_unconfigured_credential(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """The reported bug: installing `google-calendar` 502'd.

    The workflow was created and tagged before activation was attempted, so
    raising stranded it — Argus reported failure, never cached it, and the
    obvious retry made a second copy in n8n.
    """
    _seed_instance(settings, base_url="http://n8n.test")
    created_name = catalog.load_definition("google-calendar")["name"]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(
                200, {"id": "wf-gcal", "name": created_name, "active": False, "tags": []}
            )
        if request.method == "POST" and path == "/api/v1/workflows/wf-gcal/activate":
            return json_response(400, {"message": _CREDENTIAL_REFUSAL})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(
                200,
                {
                    "data": [
                        {
                            "id": "wf-gcal",
                            "name": created_name,
                            "active": False,
                            "tags": [{"name": "argus"}],
                        }
                    ]
                },
            )
        if request.method == "GET" and path == "/api/v1/tags":
            return json_response(200, {"data": [{"id": "tag-argus", "name": "argus"}]})
        if request.method == "PUT" and path.endswith("/tags"):
            return json_response(200, [{"id": "tag-argus", "name": "argus"}])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/google-calendar/install")

    # The install succeeded: the workflow exists in n8n, tagged, and Argus
    # says so rather than 502ing over a step that was never going to work yet.
    assert response.status_code == 201
    body = response.json()
    assert body["workflow_id"] == "wf-gcal"
    assert body["active"] is False
    # n8n's own words, which name the credential type the user has to grant.
    assert "Credential not configured" in body["activation_error"]
    assert body["open_in_n8n"] == "http://n8n.test/workflow/wf-gcal"

    conn = _conn(settings)
    try:
        cached = {row["id"]: row for row in store.list_workflows(conn)}
        events = store.list_events(conn)
    finally:
        conn.close()

    # Cached despite the refusal — this is what stops a retry making a second
    # copy, since the gallery can now see the template as installed.
    assert "wf-gcal" in cached
    assert cached["wf-gcal"]["active"] is False

    # And the activity feed records the install *and* why it is not live,
    # rather than silently swallowing half of what happened.
    install_events = [e for e in events if e["tag"] == "INSTALL"]
    assert len(install_events) == 1
    assert "not active" in install_events[0]["text"]
    assert "Credential not configured" in install_events[0]["text"]


def test_install_still_502s_when_the_create_itself_fails(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Activation being non-fatal must not make *creation* non-fatal.

    A failed create means nothing exists in n8n, so there is no orphan to
    protect and nothing to report as installed — 502 is still correct.
    """
    _seed_instance(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows":
            return json_response(400, {"message": "request/body/nodes is invalid"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/templates/weather/install")

    assert response.status_code == 502
    assert "could not install template" in response.json()["detail"]

    conn = _conn(settings)
    try:
        assert store.list_workflows(conn) == []
    finally:
        conn.close()


# --- POST .../workflows/{id}/activate ----------------------------------------


def test_activate_flips_the_cached_active_flag(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """The other half of the hand-off: the user granted the credential in n8n,
    and this is what makes the workflow live without leaving Argus."""
    instance = _seed_instance(settings, base_url="http://n8n.test")
    _seed_cached_workflow(settings, workflow_id="wf-gcal", name="Upcoming Events")

    activated: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/workflows/wf-gcal/activate":
            activated.append(path)
            return json_response(200, {"id": "wf-gcal", "active": True})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(
                200,
                {
                    "data": [
                        {
                            "id": "wf-gcal",
                            "name": "Upcoming Events",
                            "active": True,
                            "tags": [{"name": "argus"}],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{instance['id']}/workflows/wf-gcal/activate"
    )

    assert response.status_code == 200
    assert response.json() == {"workflow_id": "wf-gcal", "active": True}
    assert activated == ["/api/v1/workflows/wf-gcal/activate"]

    # The card is rendered off the cache, so without the refresh the button
    # would appear to do nothing.
    conn = _conn(settings)
    try:
        cached = {row["id"]: row for row in store.list_workflows(conn)}
    finally:
        conn.close()
    assert cached["wf-gcal"]["active"] is True


def test_activate_502s_when_the_credential_is_still_missing(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """Unlike install, this one *is* fatal: nothing was created, so there is no
    orphan to strand, and the refusal names exactly what the user still has to
    do in n8n."""
    instance = _seed_instance(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/activate"):
            return json_response(400, {"message": _CREDENTIAL_REFUSAL})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{instance['id']}/workflows/wf-gcal/activate"
    )

    assert response.status_code == 502
    assert "Credential not configured" in response.json()["detail"]


def test_activate_on_an_unknown_instance_is_404(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    _seed_instance(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = app_with(settings, handler)
    response = client.post("/api/automations/instances/nope/workflows/wf-gcal/activate")
    assert response.status_code == 404


# --- getting rid of a workflow ----------------------------------------------
#
# Installing a template twice leaves two workflows n8n considers unrelated
# (duplicate names are legal, each copy gets its own id), and until these
# routes existed Argus could only ever add.


def test_delete_removes_the_workflow_from_n8n_and_the_cache(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    instance = _seed_instance(settings)
    _seed_cached_workflow(settings, workflow_id="wf-dupe", name="Argus: Weather")
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE" and path == "/api/v1/workflows/wf-dupe":
            deleted.append(path)
            return json_response(200, {"id": "wf-dupe"})
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})  # gone from n8n now
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.request(
        "DELETE", f"/api/automations/instances/{instance['id']}/workflows/wf-dupe"
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert deleted == ["/api/v1/workflows/wf-dupe"]

    # Dropped by the refresh pass's own prune, not by a second copy of that
    # logic living in the delete route.
    conn = _conn(settings)
    try:
        assert store.list_workflows(conn) == []
    finally:
        conn.close()


def test_unregister_drops_only_the_argus_tag_and_keeps_the_others(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    """n8n's tag endpoint *replaces* the whole set, so unregistering has to
    resend the survivors — sending an empty list would silently strip tags the
    user added themselves."""
    instance = _seed_instance(settings)
    _seed_cached_workflow(settings, workflow_id="wf-keep", name="Argus: Weather")
    sent_tags: list[list[dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/api/v1/workflows/wf-keep":
            return json_response(
                200,
                {
                    "id": "wf-keep",
                    "tags": [
                        {"id": "tag-argus", "name": "argus"},
                        {"id": "tag-mine", "name": "personal"},
                    ],
                },
            )
        if request.method == "PUT" and path == "/api/v1/workflows/wf-keep/tags":
            sent_tags.append(json.loads(request.content))
            return json_response(200, [])
        if request.method == "GET" and path == "/api/v1/workflows":
            return json_response(200, {"data": []})  # no longer carries `argus`
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = app_with(settings, handler)
    response = client.post(
        f"/api/automations/instances/{instance['id']}/workflows/wf-keep/unregister"
    )

    assert response.status_code == 200
    assert sent_tags == [[{"id": "tag-mine"}]], "the user's own tag must survive"

    conn = _conn(settings)
    try:
        assert store.list_workflows(conn) == []
    finally:
        conn.close()


def test_delete_that_n8n_refuses_is_502_and_leaves_the_cache_alone(
    settings: Settings, fake_keyring: _FakeKeyring
) -> None:
    instance = _seed_instance(settings)
    _seed_cached_workflow(settings, workflow_id="wf-dupe", name="Argus: Weather")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return json_response(403, {"message": "forbidden"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = app_with(settings, handler)
    response = client.request(
        "DELETE", f"/api/automations/instances/{instance['id']}/workflows/wf-dupe"
    )

    assert response.status_code == 502
    # Still in n8n, so it must still be in Argus — a cache that forgot it here
    # would hide a workflow that is very much still running.
    conn = _conn(settings)
    try:
        assert [row["id"] for row in store.list_workflows(conn)] == ["wf-dupe"]
    finally:
        conn.close()
