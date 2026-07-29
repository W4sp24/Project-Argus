"""Tests for `argus mcp-server` — the vault as a tool source for coding agents.

The security-relevant property here is the *absence* of the planner's write
tools: an external agent gets read-only vault access and nothing else. That is
asserted directly rather than inferred from the tool list happening to look
right today.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent.adapters import ToolSpec, json_schema, text_result
from backend.agent.mcp_server import (
    READ_ONLY_TOOLS,
    SERVER_NAME,
    build_server,
    client_config_snippets,
)
from backend.core.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault)


def fake_tools() -> list[ToolSpec]:
    """Stand-ins for chat's real tools, so no embedding model is loaded."""

    async def search(args: dict) -> dict:
        return text_result({"results": [{"path": "50-Reference/algorithms.md", "text": args}]})

    async def read(args: dict) -> dict:
        return text_result(f"contents of {args['path']}")

    async def tasks(_args: dict) -> dict:
        return text_result({"today": [], "overdue": []})

    return [
        ToolSpec("search_vault", "search", json_schema({"query": {"type": "string"}}), search),
        ToolSpec("read_note", "read", json_schema({"path": {"type": "string"}}), read),
        ToolSpec("list_tasks", "tasks", json_schema({}), tasks),
    ]


async def handler_for(server, kind: str):
    """The server's registered handler for one MCP request type."""
    from mcp import types

    request_type = {
        "list": types.ListToolsRequest,
        "call": types.CallToolRequest,
    }[kind]
    return server.request_handlers[request_type]


async def list_tools(server) -> list:
    from mcp import types

    handler = await handler_for(server, "list")
    result = await handler(types.ListToolsRequest(method="tools/list"))
    return result.root.tools


async def call_tool(server, name: str, arguments: dict) -> str:
    from mcp import types

    handler = await handler_for(server, "call")
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(request)
    return "".join(block.text for block in result.root.content)


# --- exposure ---------------------------------------------------------------


@pytest.mark.anyio
async def test_exposes_exactly_the_read_only_tools(settings: Settings) -> None:
    server = build_server(settings, tools=fake_tools())

    names = {tool.name for tool in await list_tools(server)}

    assert names == set(READ_ONLY_TOOLS)


@pytest.mark.anyio
async def test_planner_write_tools_are_never_exposed(settings: Settings) -> None:
    """An external agent has no in-app planner context, so it may not propose.

    Suggestions it queued would land in the user's Review queue looking exactly
    like well-informed ones.
    """
    planner_tools = fake_tools() + [
        ToolSpec("propose_schedule", "write", json_schema({}), fake_tools()[0].handler),
        ToolSpec("propose_note_edit", "write", json_schema({}), fake_tools()[0].handler),
    ]
    # Even handed the planner's tools, the allow-list filter is what ships.
    filtered = [spec for spec in planner_tools if spec.name in READ_ONLY_TOOLS]
    server = build_server(settings, tools=filtered)

    names = {tool.name for tool in await list_tools(server)}
    assert not any(name.startswith("propose_") for name in names)


@pytest.mark.anyio
async def test_an_unlisted_tool_is_refused_not_dispatched(settings: Settings) -> None:
    """MCP clients can send anything; the server does not trust the caller."""
    server = build_server(settings, tools=fake_tools())

    reply = await call_tool(server, "propose_note_edit", {"path": "x", "diff": "y"})

    assert "unknown tool" in reply


@pytest.mark.anyio
async def test_tool_schemas_travel_intact(settings: Settings) -> None:
    server = build_server(settings, tools=fake_tools())

    tools = {tool.name: tool for tool in await list_tools(server)}

    assert tools["read_note"].inputSchema["properties"]["path"]["type"] == "string"
    assert tools["read_note"].inputSchema["required"] == ["path"]
    assert tools["list_tasks"].inputSchema["properties"] == {}


# --- dispatch ---------------------------------------------------------------


@pytest.mark.anyio
async def test_search_through_the_transport_matches_the_in_process_shape(
    settings: Settings,
) -> None:
    """The same handler backs both, so results must not diverge by route."""
    tools = fake_tools()
    server = build_server(settings, tools=tools)

    over_mcp = await call_tool(server, "search_vault", {"query": "dijkstra"})
    in_process = await tools[0].handler({"query": "dijkstra"})

    assert json.loads(over_mcp) == json.loads(in_process["content"][0]["text"])


@pytest.mark.anyio
async def test_handler_failures_come_back_as_text_not_a_dead_server(
    settings: Settings,
) -> None:
    async def explode(_args: dict) -> dict:
        raise RuntimeError("index is cold")

    server = build_server(settings, tools=[ToolSpec("read_note", "read", json_schema({}), explode)])

    assert "index is cold" in await call_tool(server, "read_note", {})


# --- docs -------------------------------------------------------------------


def test_config_snippets_cover_all_three_clis() -> None:
    snippets = client_config_snippets()

    assert set(snippets) == {"claude-code", "codex-cli", "gemini-cli"}
    assert snippets["claude-code"].startswith("claude mcp add argus")
    parsed = json.loads(snippets["codex-cli"])
    assert parsed["mcpServers"][SERVER_NAME] == {"command": "argus", "args": ["mcp-server"]}


def test_cli_exposes_the_mcp_server_subcommand() -> None:
    """`argus mcp-server` must parse; it is what every config snippet invokes."""
    from backend.cli import main

    with pytest.raises(SystemExit):
        main(["mcp-server", "--help"])
