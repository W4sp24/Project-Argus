"""``argus mcp-server`` — the vault as a tool source for any coding agent.

This is a different thing from the dashboard's chat. Chat is Argus asking a
model about your vault; this lets *your* terminal agent — Claude Code, Codex
CLI, Gemini CLI, anything that speaks MCP — read your vault while you work in
some unrelated project directory.

It reuses Part 1's :class:`~backend.agent.adapters.ToolSpec` handlers rather
than reimplementing vault access per CLI, so a search here returns exactly what
in-app chat's search returns. All three CLIs already speak MCP, so one standard
stdio server replaces three bespoke integrations.

**Read-only, deliberately.** Only ``search_vault``, ``read_note`` and
``list_tasks`` are exposed. The planner's ``propose_*`` tools are not: an
external agent queuing suggestions without the in-app planner's context — the
preferences note, dismissal feedback, task buckets — would produce worse
proposals than no proposals, and they would land in the user's Review queue
looking identical to good ones. Revisit once the read-only path is proven.
"""

from __future__ import annotations

import json
from typing import Any

from backend.agent.adapters import ToolSpec, flatten_tool_result
from backend.config import Settings

SERVER_NAME = "argus"

# The subset an external agent may use. Kept as an explicit allow-list rather
# than "everything except propose_*", so a tool added to chat later is not
# silently exposed to every coding agent on the machine.
READ_ONLY_TOOLS = ("search_vault", "read_note", "list_tasks")


def read_only_tools(settings: Settings) -> list[ToolSpec]:
    """Chat's vault tools, filtered to the read-only allow-list."""
    from backend.agent.runtime import build_vault_tools
    from backend.rag.index import VaultIndex

    index = VaultIndex(settings.db_path.parent / "chroma")
    tools = build_vault_tools(settings, index, model_label="mcp")
    return [spec for spec in tools if spec.name in READ_ONLY_TOOLS]


def build_server(settings: Settings, tools: list[ToolSpec] | None = None) -> Any:
    """An MCP server exposing the read-only vault tools.

    ``tools`` is injectable so tests can exercise the transport without
    loading the embedding model.
    """
    from mcp.server import Server

    specs = tools if tools is not None else read_only_tools(settings)
    by_name = {spec.name: spec for spec in specs}
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        from mcp import types

        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.parameters,
            )
            for spec in specs
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        from mcp import types

        spec = by_name.get(name)
        if spec is None:
            # Never reachable through list_tools, but an MCP client may send
            # anything — refuse rather than trusting the caller.
            return [types.TextContent(type="text", text=f"error: unknown tool {name!r}")]
        try:
            result = await spec.handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - the agent sees the reason
            return [types.TextContent(type="text", text=f"error: {exc}")]
        return [types.TextContent(type="text", text=flatten_tool_result(result))]

    return server


async def serve_stdio(settings: Settings) -> None:
    """Run the server over stdio — how every local MCP client attaches."""
    from mcp.server.stdio import stdio_server

    server = build_server(settings)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def client_config_snippets(command: str = "argus mcp-server") -> dict[str, str]:
    """Copy-pasteable config for each supported CLI, for the docs and /system."""
    argv = command.split()
    stdio_entry = {"command": argv[0], "args": argv[1:]}
    return {
        "claude-code": f"claude mcp add {SERVER_NAME} -s user -- {command}",
        "codex-cli": json.dumps({"mcpServers": {SERVER_NAME: stdio_entry}}, indent=2),
        "gemini-cli": json.dumps({"mcpServers": {SERVER_NAME: stdio_entry}}, indent=2),
    }
