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
import logging
from pathlib import Path
from typing import Any

from backend.agent.adapters import ToolSpec, flatten_tool_result
from backend.core.config import Settings

logger = logging.getLogger("argus.rag")

SERVER_NAME = "argus"

# The subset an external agent may use. Kept as an explicit allow-list rather
# than "everything except propose_*", so a tool added to chat later is not
# silently exposed to every coding agent on the machine.
READ_ONLY_TOOLS = ("search_vault", "read_note", "list_tasks")


def read_only_tools(settings: Settings) -> list[ToolSpec]:
    """Chat's vault tools, filtered to the read-only allow-list.

    Warming happens here for the same reason it happens when the chat socket
    connects: a cold embedding-model load costs ~20s, and paying it inside the
    first ``search_vault`` call stalls the stdio server's event loop until it
    finishes — long enough that the calling agent may time the tool out with no
    explanation. Warming on a background thread and gating searches on the
    ``ready`` event turns that into a wait the caller can actually survive.
    """
    import threading

    from backend.agent.runtime import build_vault_tools
    from backend.rag.index import VaultIndex

    index = VaultIndex(settings.db_path.parent / "chroma", taxonomy=settings.taxonomy)
    ready = threading.Event()

    def warm() -> None:
        try:
            # Best-effort, exactly as ChatAgent.warm: a failed warm must release
            # waiters so a search fails on its own terms instead of hanging.
            # Logged (not silently swallowed) so a genuinely broken index isn't
            # invisible to anyone but the caller of the first real search.
            index.query("warmup", n_results=1)
        except Exception:
            logger.warning(
                "mcp index warm-up failed (will retry on first real query)", exc_info=True
            )
        finally:
            ready.set()

    threading.Thread(target=warm, daemon=True).start()
    tools = build_vault_tools(settings, index, model_label="mcp", ready=ready)
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


def default_command() -> list[str]:
    """How to invoke this bridge *on this install*.

    The desktop app ships ``argus-backend.exe``, not the ``argus`` console
    script, so handing a desktop user ``argus mcp-server`` gave them a command
    their machine does not have. Frozen builds get their own executable path.
    """
    import sys

    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp-server"]
    return ["argus", "mcp-server"]


def client_config_snippets(
    command: str | None = None, env_file: Path | None = None
) -> dict[str, str]:
    """Copy-pasteable config for each supported CLI, for the docs and /system.

    Every snippet carries ``ARGUS_ENV_FILE``. Without it, a coding agent that
    spawns this command from *its own* project directory (Claude Code, Codex,
    Gemini CLI — none of them run from this repo) makes ``Settings.load()``
    resolve ``VAULT_PATH`` against ``./.env`` relative to *their* cwd, finds
    nothing, and the bridge exits 1 (see
    ``desktop/backend/argus_server.py``'s ``_cmd_mcp_server`` /
    ``backend.cli``'s ``mcp-server`` branch — both already fail cleanly on an
    unconfigured vault, but "cleanly" isn't "correctly": the vault this
    process is actually running against is not unconfigured, the *lookup* is
    just pointed at the wrong file). Resolving it here means the pasted
    snippet keeps working regardless of where the calling agent's cwd is.

    ``claude mcp add`` takes environment variables via repeated ``--env
    KEY=value`` flags before the server name (verified against the CLI's own
    docs — not guessed); the two JSON-config clients take a plain ``env``
    object on the stdio entry.
    """
    from backend.core.config import DEFAULT_ENV_FILE

    argv = command.split() if command else default_command()
    command = " ".join(argv)
    resolved_env = str((env_file or DEFAULT_ENV_FILE).resolve())
    stdio_entry = {"command": argv[0], "args": argv[1:], "env": {"ARGUS_ENV_FILE": resolved_env}}
    return {
        "claude-code": (
            f"claude mcp add {SERVER_NAME} -s user "
            f"--env ARGUS_ENV_FILE={resolved_env} -- {command}"
        ),
        "codex-cli": json.dumps({"mcpServers": {SERVER_NAME: stdio_entry}}, indent=2),
        "gemini-cli": json.dumps({"mcpServers": {SERVER_NAME: stdio_entry}}, indent=2),
    }
