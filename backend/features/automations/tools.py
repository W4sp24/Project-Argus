"""Registered automations, exposed to the agent as callable tools.

A workflow with a typed field schema already *is* a
:class:`~backend.agent.adapters.ToolSpec` in everything but name: it has a
stable identifier, a human description, and a JSON-Schema-shaped parameter
list. So "run my meeting prep" works in the chat dock for the same reason the
RUN button works, at close to no additional cost.

**These tools are deliberately excluded from the MCP surface.** The allow-list
in :mod:`backend.agent.mcp_server` is explicit precisely so that a tool added
to chat later is not silently handed to every coding agent on the machine, and
firing an automation is a *write* action that can send email, create calendar
events, or move money-adjacent things around. Chat is a surface the user is
sitting in front of; an MCP client is not. Adding these to ``READ_ONLY_TOOLS``
would be a mistake, not a convenience.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from backend.agent.adapters import ToolSpec, json_schema, text_result
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.automations.schema import (
    AutomationSchema,
    parse_workflow,
    requires_confirmation,
)

logger = logging.getLogger("argus.automations.tools")

#: Tool names must be a stable, model-friendly identifier. n8n workflow names
#: are free text ("Meeting prep — v2 (final)"), so they are slugged.
_UNSAFE = re.compile(r"[^a-z0-9]+")

TOOL_PREFIX = "run_automation_"


def tool_name_for(workflow_name: str, workflow_id: str) -> str:
    """A stable tool name for a workflow.

    Falls back to the workflow id when the name slugs to nothing (a workflow
    named entirely in non-Latin script, say) so the tool is still callable.
    """
    slug = _UNSAFE.sub("_", (workflow_name or "").lower()).strip("_")
    return f"{TOOL_PREFIX}{slug or workflow_id.lower()}"


def _parameters(parsed: AutomationSchema) -> dict[str, Any]:
    """The workflow's form fields as a JSON Schema object.

    Only the fields a caller can meaningfully supply are exposed: hidden fields
    carry their own defaults through, and password fields are omitted entirely
    — relaying a secret through a model's context is not something to do
    silently, and no automation worth running needs the agent to invent one.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in parsed.fields:
        if getattr(field, "hidden", False) or getattr(field, "secret", False):
            continue
        if field.type == "custom_html":
            continue  # presentational, not an input
        properties[field.name] = {
            "type": "number" if field.type == "number" else "string",
            "description": field.label or field.name,
        }
        if field.options:
            properties[field.name]["enum"] = list(field.options)
        if field.required:
            required.append(field.name)
    return json_schema(properties, required)


def build_automation_tools(settings: Settings) -> list[ToolSpec]:
    """One ToolSpec per registered, runnable automation.

    Returns an empty list when no n8n instance is registered or the cache is
    empty — the agent simply has no automation tools, which is the correct
    behaviour rather than an error. Never raises: a broken automations store
    must not take chat down with it.
    """
    try:
        conn: sqlite3.Connection = connect(settings.db_path)
    except Exception:  # noqa: BLE001 - chat must survive a bad db
        logger.exception("could not open the automations store; exposing no tools")
        return []

    try:
        init_schema(conn)
        rows = store.list_workflows(conn)
    except Exception:  # noqa: BLE001
        logger.exception("could not read registered automations; exposing no tools")
        return []
    finally:
        conn.close()

    specs: list[ToolSpec] = []
    for row in rows:
        definition = row.get("schema_json") or {}
        try:
            parsed = parse_workflow(definition)
        except Exception:  # noqa: BLE001 - one bad workflow must not sink the belt
            logger.debug("skipping unparseable workflow %r", row.get("id"))
            continue
        if parsed.kind == "none":
            continue  # a schedule-driven workflow is not something to invoke

        workflow_id = str(row.get("id"))
        confirm = requires_confirmation(definition)
        description = (
            f"Run the n8n automation {row.get('name') or workflow_id!r}."
            + (
                " This automation is marked as needing confirmation, so describe"
                " what it will do and get explicit agreement before calling it."
                if confirm
                else ""
            )
        )

        specs.append(
            ToolSpec(
                name=tool_name_for(str(row.get("name") or ""), workflow_id),
                description=description,
                parameters=_parameters(parsed),
                handler=_make_handler(settings, workflow_id),
            )
        )
    return specs


def _make_handler(settings: Settings, workflow_id: str):
    """Bind a run handler to one workflow id.

    The handler drives the **same HTTP route the RUN button drives**, in
    process, over an ASGI transport. That is deliberate. The alternative was to
    lift the run logic out of the router's closure and call it directly, which
    would either mean a sizeable refactor of a tested file or — far worse — a
    second copy of the dispatch rules. Two run paths would drift, and the one
    that drifts silently is the one nobody is looking at: the in-flight lock,
    the confirm flag, the widget-payload validation and the run-history rows
    all have to behave identically whether a human pressed a button or the
    agent decided to. One path, one set of rules.
    """

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        # Imported lazily: the router imports this module's siblings, and a
        # module-scope import here would close that loop.
        import httpx
        from fastapi import FastAPI

        from backend.features.automations.router import build_automations_router

        app = FastAPI()
        app.include_router(build_automations_router(settings))
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://automations.invalid"
            ) as client:
                response = await client.post(
                    f"/api/automations/{workflow_id}/run",
                    json={"payload": arguments or {}},
                    timeout=None,
                )
        except Exception as exc:  # noqa: BLE001 - a tool must report, not crash
            logger.exception("automation %s failed from the agent", workflow_id)
            return text_result(f"The automation could not be run: {exc}")

        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            return text_result(f"The automation did not run: {detail}")
        return text_result(response.json())

    return handler
