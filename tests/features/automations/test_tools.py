"""Registered automations exposed to the agent as tools.

The security-relevant assertion here is the negative one: these are write
actions, and they must not reach the MCP surface that every coding agent on
the machine can call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store, tools

FORM_WORKFLOW = {
    "name": "Meeting prep — v2 (final)",
    "tags": [{"name": "argus"}],
    "nodes": [
        {
            "type": "n8n-nodes-base.formTrigger",
            "webhookId": "abc123",
            "parameters": {
                "formFields": {
                    "values": [
                        {"fieldLabel": "topic", "fieldType": "text", "requiredField": True},
                        {"fieldLabel": "notes", "fieldType": "textarea"},
                        {"fieldLabel": "secret", "fieldType": "password"},
                        {"fieldLabel": "hidden_id", "fieldType": "hiddenField"},
                        {
                            "fieldLabel": "mode",
                            "fieldType": "dropdown",
                            "fieldOptions": {"values": [{"option": "fast"}, {"option": "deep"}]},
                        },
                    ]
                }
            },
        }
    ],
}

SCHEDULE_WORKFLOW = {
    "name": "Nightly sync",
    "tags": [{"name": "argus"}],
    "nodes": [{"type": "n8n-nodes-base.scheduleTrigger", "parameters": {}}],
}


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    return Settings(_vault_path=root)


def _seed(settings: Settings, *workflows) -> None:
    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        for index, definition in enumerate(workflows):
            store.upsert_workflow(
                conn,
                str(index),
                name=definition["name"],
                tags=json.dumps([t["name"] for t in definition.get("tags", [])]),
                schema_json=definition,
                active=True,
            )
    finally:
        conn.close()


def test_no_registered_automations_means_no_tools(settings: Settings) -> None:
    """The agent simply has no automation tools — not an error."""
    assert tools.build_automation_tools(settings) == []


def test_a_form_workflow_becomes_a_callable_tool(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    specs = tools.build_automation_tools(settings)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name.startswith(tools.TOOL_PREFIX)
    assert " " not in spec.name  # a free-text n8n name must be slugged
    assert spec.parameters["type"] == "object"


def test_password_and_hidden_fields_are_not_exposed_to_the_model(
    settings: Settings,
) -> None:
    """Relaying a secret through a model's context is not done silently."""
    _seed(settings, FORM_WORKFLOW)
    properties = tools.build_automation_tools(settings)[0].parameters["properties"]
    assert "secret" not in properties
    assert "hidden_id" not in properties
    assert "topic" in properties and "notes" in properties


def test_dropdown_options_become_an_enum(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    properties = tools.build_automation_tools(settings)[0].parameters["properties"]
    assert properties["mode"]["enum"] == ["fast", "deep"]


def test_required_fields_are_marked_required(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    params = tools.build_automation_tools(settings)[0].parameters
    assert "topic" in params["required"]
    assert "notes" not in params["required"]


def test_a_schedule_workflow_is_not_invokable(settings: Settings) -> None:
    """Nothing to invoke — it may still own a widget, but it is not a tool."""
    _seed(settings, SCHEDULE_WORKFLOW)
    assert tools.build_automation_tools(settings) == []


def test_a_confirm_tagged_workflow_says_so_in_its_description(
    settings: Settings,
) -> None:
    definition = dict(FORM_WORKFLOW)
    definition["tags"] = [{"name": "argus"}, {"name": "argus:confirm"}]
    _seed(settings, definition)
    description = tools.build_automation_tools(settings)[0].description
    assert "confirmation" in description.lower()


def test_an_unparseable_workflow_does_not_sink_the_belt(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW, {"name": "broken", "nodes": "not-a-list"})
    specs = tools.build_automation_tools(settings)
    assert len(specs) == 1  # the good one survives


def test_tool_names_are_stable_and_slugged() -> None:
    assert tools.tool_name_for("Meeting prep — v2 (final)", "id1").isascii()
    assert " " not in tools.tool_name_for("Meeting prep", "id1")
    # A name that slugs to nothing still yields a callable tool.
    assert tools.tool_name_for("日本語", "wf7").endswith("wf7")


def test_automation_tools_are_not_on_the_mcp_surface() -> None:
    """These are write actions. An MCP client is not a user sitting in front
    of the app, and the allow-list is explicit precisely so a tool added to
    chat later is not silently handed to every coding agent on the machine."""
    from backend.agent.mcp_server import READ_ONLY_TOOLS

    assert not any(name.startswith(tools.TOOL_PREFIX) for name in READ_ONLY_TOOLS)
    assert set(READ_ONLY_TOOLS) == {"search_vault", "read_note", "list_tasks"}


# --- the agent runs on the right instance ------------------------------------


# --- tool summarizers --------------------------------------------------------


def test_automation_tools_build_with_a_summarizer_set(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    spec = tools.build_automation_tools(settings)[0]
    assert spec.summarize is not None


def test_a_successful_run_summary_names_the_workflow_and_is_ok(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    spec = tools.build_automation_tools(settings)[0]
    result_text = json.dumps({"run_id": "1", "status": "ok"})

    summary = spec.summarize({"topic": "standup"}, result_text)

    assert summary.ok is True
    assert "Meeting prep" in summary.detail
    assert summary.paths == ()


def test_a_failed_run_status_summary_is_not_ok(settings: Settings) -> None:
    _seed(settings, FORM_WORKFLOW)
    spec = tools.build_automation_tools(settings)[0]
    result_text = json.dumps({"run_id": "1", "status": "failed"})

    summary = spec.summarize({}, result_text)

    assert summary.ok is False


def test_the_handlers_own_plain_text_failure_is_recognized_as_not_ok(settings: Settings) -> None:
    """The handler's failure strings ("The automation could not be run: ...")
    are plain text, not JSON, and carry no "error:" prefix the generic
    fallback could key off — the summarizer must catch this itself."""
    _seed(settings, FORM_WORKFLOW)
    spec = tools.build_automation_tools(settings)[0]

    summary = spec.summarize({}, "The automation did not run: boom")

    assert summary.ok is False


def test_agent_tool_targets_the_workflows_own_instance(tmp_path: Path, monkeypatch) -> None:
    """Workflow ids are unique only within one n8n, so two registered
    instances can hand out the same id. The unscoped route has to resolve
    across every instance and refuses with a 409 when they collide — which
    would leave the agent unable to run an automation the UI runs fine. The
    cache row already knows the instance, so nothing needs resolving.
    """
    import asyncio

    settings = Settings(_vault_path=tmp_path / "vault")
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)

    conn = connect(settings.db_path)
    init_schema(conn)
    # The same n8n workflow id on two different instances.
    for instance in ("alpha", "bravo"):
        store.upsert_workflow(
            conn,
            "7",
            name=f"Prep on {instance}",
            tags=["argus"],
            schema_json=FORM_WORKFLOW,
            active=True,
            instance_id=instance,
        )
    conn.close()

    specs = tools.build_automation_tools(settings)
    assert len(specs) == 2, [spec.name for spec in specs]

    called: list[str] = []

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"status": "ok"}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, path, **kwargs):
            called.append(path)
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    asyncio.run(specs[0].handler({}))

    assert called, "the handler never issued a request"
    # Scoped to an instance, not the ambiguous unscoped route.
    assert "/instances/" in called[0], called[0]
    assert called[0].endswith("/workflows/7/run"), called[0]
    assert any(instance in called[0] for instance in ("alpha", "bravo")), called[0]
