"""Connect to an *external* MCP server and ask what tools it has.

The MCP analogue of :func:`backend.agent.adapters.probe_tool_calling`, and it
exists for the same reason: without it, "Connect" means "we wrote your text
into a JSON file" and the user finds out the command was wrong the first time
they actually need the thing. Here they find out immediately, and the tool list
that comes back is proof the handshake really happened.

Note the direction. :mod:`backend.agent.mcp_server` makes Argus an MCP
*server* for your coding agents; this makes Argus an MCP *client* of somebody
else's server. Both use the same ``mcp`` package, which is a direct dependency.

Everything is bounded and best-effort: a hard timeout, the subprocess always
reaped, and any failure returned as ``ok=False`` with a readable reason rather
than raised. A server that hangs on start must not hang the request.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from pydantic import BaseModel

#: A stdio server that has not spoken by now is not going to.
PROBE_TIMEOUT_SECONDS = 20.0


class McpProbeResult(BaseModel):
    """What the Test button renders."""

    ok: bool
    detail: str
    latency_ms: int = 0
    tools: list[str] = []


def _elapsed_ms(started: float) -> int:
    return int((asyncio.get_event_loop().time() - started) * 1000)


async def _probe(entry: dict[str, Any], token: str | None) -> McpProbeResult:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    started = asyncio.get_event_loop().time()
    transport = entry.get("transport", "stdio")

    if transport == "http":
        from mcp.client.streamable_http import streamablehttp_client

        url = (entry.get("url") or "").strip()
        if not url:
            return McpProbeResult(ok=False, detail="an http server needs a URL")
        headers = dict(entry.get("headers") or {})
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
        async with (
            streamablehttp_client(url, headers=headers) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        return McpProbeResult(
            ok=True,
            detail=f"connected — {len(names)} tool{'' if len(names) == 1 else 's'} available",
            latency_ms=_elapsed_ms(started),
            tools=names,
        )

    command = (entry.get("command") or "").strip()
    if not command:
        return McpProbeResult(ok=False, detail="a stdio server needs a command to run")
    if shutil.which(command) is None:
        return McpProbeResult(
            ok=False,
            detail=f"{command!r} is not on your PATH — check the command name or install it",
        )

    params = StdioServerParameters(
        command=command,
        args=[str(arg) for arg in (entry.get("args") or [])],
        # The server inherits the environment plus anything the user set, so a
        # command that needs a token in env works the same way it does in their
        # own shell.
        env={**os.environ, **{str(k): str(v) for k, v in (entry.get("env") or {}).items()}},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
    names = [tool.name for tool in listed.tools]
    return McpProbeResult(
        ok=True,
        detail=f"connected — {len(names)} tool{'' if len(names) == 1 else 's'} available",
        latency_ms=_elapsed_ms(started),
        tools=names,
    )


async def probe_server(entry: dict[str, Any], token: str | None = None) -> McpProbeResult:
    """Handshake with one server and list its tools. Never raises.

    ``token`` carries a bearer the user has typed but not yet saved, so a
    configuration can be verified before anything reaches the keyring.
    """
    try:
        return await asyncio.wait_for(_probe(entry, token), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return McpProbeResult(
            ok=False,
            detail=f"no response within {int(PROBE_TIMEOUT_SECONDS)}s — the server did not start "
            "or did not complete the MCP handshake",
        )
    except FileNotFoundError as exc:
        return McpProbeResult(ok=False, detail=f"could not run that command: {exc}")
    except Exception as exc:  # noqa: BLE001 - the user sees the reason, not a 500
        return McpProbeResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
