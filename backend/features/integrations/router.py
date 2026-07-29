"""INTEGRATIONS endpoints (redesign §12) — connectors and external MCP servers.

The panels these serve used to be a facade: hardcoded rows whose CONNECT
button fired a toast naming a CLI command, two of which advertised servers that
existed nowhere in the backend and one of which (``argus mcp add``) was never
implemented. Nothing could actually be connected from the app.

Modelled on :mod:`backend.features.system.models`, the one genuinely-wired
"register a thing with credentials, verify it, list and delete it" flow in the
repo, and holding the same line on secrets: tokens go to the OS keyring (I4)
and only ``has_key``-style booleans cross the API.

Verification before persistence is the point throughout. Storing a Todoist
token that turns out to be wrong means the failure surfaces during tomorrow's
07:00 briefing instead of while the user is looking at the dialog they typed
it into.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agent.credentials import (
    KEY_ABSENT,
    KEY_PRESENT,
    CredentialError,
    delete_key,
    key_state,
    store_key,
)
from backend.connectors import gcal, todoist
from backend.core.config import Settings
from backend.core.jsonstore import save_json
from backend.features.integrations import store
from backend.features.integrations.mcp_client import McpProbeResult, probe_server

#: Abandoned browser consent must not pin a worker thread for the process's life.
OAUTH_TIMEOUT_SECONDS = 180.0

McpProber = Callable[[dict[str, Any], str | None], Awaitable[McpProbeResult]]


class ConnectorInfo(BaseModel):
    """One first-party connector's live state."""

    id: str
    name: str
    #: "wired" | "not-connected" | "needs-credentials"
    status: str
    detail: str
    #: Whether the UI can offer a connect flow (vs an informational row).
    can_connect: bool = True


class McpServerInfo(BaseModel):
    """One registered external MCP server. Never carries its bearer token (I4)."""

    name: str
    transport: str
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    #: Tool names captured when the server last passed a test.
    tools: list[str] = []
    has_key: bool = False
    #: "present" | "absent" | "unknown" — see backend.agent.credentials.
    key_state: str = KEY_ABSENT


class IntegrationsResponse(BaseModel):
    """``GET /api/integrations``."""

    connectors: list[ConnectorInfo]
    mcp_servers: list[McpServerInfo]
    #: True once Argus can act as an MCP client — see the note on the hub card.
    mcp_tools_in_chat: bool = False


class TodoistConnectRequest(BaseModel):
    token: str


class GcalCredentialsRequest(BaseModel):
    """The Google OAuth *client* JSON, pasted or uploaded."""

    credentials_json: str


class ConnectResult(BaseModel):
    ok: bool
    detail: str


class McpServerRequest(BaseModel):
    """Register or test an external MCP server."""

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    headers: dict[str, str] = {}
    env: dict[str, str] = {}
    token: str | None = None
    #: Registration verifies the handshake by default (mirrors AddModelRequest).
    verify: bool = True


def _entry_from(request: McpServerRequest) -> dict[str, Any]:
    return {
        "name": request.name.strip(),
        "transport": request.transport,
        "command": (request.command or "").strip() or None,
        "args": [arg for arg in request.args if arg],
        "url": (request.url or "").strip() or None,
        "headers": dict(request.headers),
        "env": dict(request.env),
    }


def _validated(request: McpServerRequest) -> dict[str, Any]:
    name = request.name.strip()
    if not store.SERVER_NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="invalid server name")
    if request.transport not in store.TRANSPORTS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown transport {request.transport!r} "
            f"(expected one of {', '.join(store.TRANSPORTS)})",
        )
    entry = _entry_from(request)
    if request.transport == "stdio" and not entry["command"]:
        raise HTTPException(status_code=422, detail="a stdio server needs a command")
    if request.transport == "http":
        url = entry["url"]
        if not url or not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="url must be an http(s) URL")
    return entry


def build_integrations_router(
    settings: Settings, mcp_prober: McpProber | None = None
) -> APIRouter:
    """The /api/integrations routes.

    No prefix of its own: the system router mounts this under its ``/api``.
    ``mcp_prober`` is injectable for the same reason the model registry's
    ``Prober`` is — it spawns processes and opens sockets, and tests must not.
    """
    router = APIRouter()
    probe = mcp_prober or probe_server

    def _credentials_path() -> Path:
        """The uploaded client file, falling back to the historical repo-root one.

        Existing setups put ``credentials.json`` beside the repo; a packaged
        desktop install has no such place, so uploads land in ``.argus/``. The
        fallback keeps anyone who already ran ``argus connect gcal`` working.
        """
        uploaded = settings.gcal_credentials_file
        if uploaded.is_file():
            return uploaded
        return gcal.CREDENTIALS_FILE

    def _connectors() -> list[ConnectorInfo]:
        gcal_ready = gcal.configured()
        has_client = _credentials_path().is_file()
        return [
            ConnectorInfo(
                id="gcal",
                name="Google Calendar",
                status="wired"
                if gcal_ready
                else ("not-connected" if has_client else "needs-credentials"),
                detail=(
                    "merged into PLANNER.TIMELINE"
                    if gcal_ready
                    else (
                        "OAuth client saved — finish the browser consent to connect"
                        if has_client
                        else "needs a Desktop OAuth client from Google Cloud Console"
                    )
                ),
            ),
            ConnectorInfo(
                id="todoist",
                name="Todoist",
                status="wired" if todoist.configured() else "not-connected",
                detail=(
                    "merged into TASKS.DUE"
                    if todoist.configured()
                    else "needs a personal API token"
                ),
            ),
        ]

    def _server_info(entry: dict[str, Any]) -> McpServerInfo:
        state = key_state(entry.get("key_ref"))  # one keyring read, two fields
        return McpServerInfo(
            name=entry["name"],
            transport=entry.get("transport", "stdio"),
            command=entry.get("command"),
            args=list(entry.get("args") or []),
            url=entry.get("url"),
            tools=list(entry.get("tools") or []),
            has_key=state == KEY_PRESENT,
            key_state=state,
        )

    def _servers() -> list[McpServerInfo]:
        return [
            _server_info(entry) for entry in store.load_servers(settings.mcp_servers_file)
        ]

    @router.get("/integrations", response_model=IntegrationsResponse)
    def list_integrations() -> IntegrationsResponse:
        return IntegrationsResponse(connectors=_connectors(), mcp_servers=_servers())

    @router.post("/integrations/todoist/connect", response_model=ConnectResult)
    def connect_todoist(request: TodoistConnectRequest) -> ConnectResult:
        """Verify the token against Todoist, then store it — in that order."""
        token = request.token.strip()
        if not token:
            raise HTTPException(status_code=422, detail="empty Todoist token")

        verified = ""
        try:
            from todoist_api_python.api import TodoistAPI

            todoist.list_tasks(TodoistAPI(token))
            verified = " and verified"
        except ImportError:
            # The Todoist client is an optional dependency; without it the token
            # is still worth storing, but say plainly that nothing was checked.
            verified = " (not verified — the Todoist client library is not installed)"
        except Exception as exc:  # noqa: BLE001 - a rejected token is normal here
            raise HTTPException(
                status_code=422, detail=f"Todoist rejected that token: {exc}"
            ) from exc

        todoist.connect(token)
        return ConnectResult(ok=True, detail=f"Todoist connected{verified}")

    @router.post("/integrations/gcal/credentials", response_model=ConnectResult)
    def upload_gcal_credentials(request: GcalCredentialsRequest) -> ConnectResult:
        """Store the OAuth client file so the consent flow has something to run."""
        import json

        try:
            payload = json.loads(request.credentials_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"that is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or not ({"installed", "web"} & set(payload)):
            raise HTTPException(
                status_code=422,
                detail="that JSON has no 'installed' or 'web' key — download the client "
                "secret for a *Desktop* OAuth client and paste that file",
            )

        # The parsed payload, written atomically: a half-written OAuth client
        # file fails deep inside google-auth with a far worse error than "that
        # is not valid JSON".
        save_json(settings.gcal_credentials_file, payload)
        return ConnectResult(ok=True, detail="OAuth client saved — now finish the consent flow")

    @router.post("/integrations/gcal/connect", response_model=ConnectResult)
    def connect_gcal() -> ConnectResult:
        """Run the browser consent flow. Sync on purpose.

        FastAPI runs a plain ``def`` endpoint in a threadpool, which is exactly
        what this needs: ``run_local_server`` blocks on a real browser
        round-trip and must stay off the event loop.
        """
        credentials = _credentials_path()
        if not credentials.is_file():
            raise HTTPException(
                status_code=409,
                detail="upload your Google OAuth client JSON first",
            )
        try:
            gcal.connect(credentials, timeout_seconds=OAUTH_TIMEOUT_SECONDS)
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail="Google Calendar support is not installed in this build "
                f"({exc}). Install the google-auth-oauthlib extra.",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - the user sees why, not a 500
            raise HTTPException(status_code=422, detail=f"consent did not complete: {exc}") from exc
        return ConnectResult(ok=True, detail="Google Calendar connected")

    @router.delete("/integrations/{connector_id}", response_model=ConnectResult)
    def disconnect(connector_id: str) -> ConnectResult:
        """Forget a connector's stored credential."""
        if connector_id == "gcal":
            gcal.disconnect()
        elif connector_id == "todoist":
            todoist.disconnect()
        else:
            raise HTTPException(status_code=404, detail=f"no connector {connector_id}")
        return ConnectResult(ok=True, detail=f"{connector_id} disconnected")

    @router.post("/integrations/mcp/test", response_model=McpProbeResult)
    async def test_mcp_server(request: McpServerRequest) -> McpProbeResult:
        """Handshake with a server and list its tools, saving nothing.

        The typed-but-unsaved bearer token travels in memory for this one call.
        """
        entry = _validated(request)
        return await probe(entry, request.token)

    @router.post("/integrations/mcp", response_model=McpServerInfo, status_code=201)
    async def add_mcp_server(request: McpServerRequest) -> McpServerInfo:
        entry = _validated(request)
        name = entry["name"]
        if any(server.name == name for server in _servers()):
            raise HTTPException(status_code=409, detail=f"server {name} already exists")

        tools: list[str] = []
        if request.verify:
            result = await probe(entry, request.token)
            if not result.ok:
                raise HTTPException(
                    status_code=422, detail=f"{name} did not connect: {result.detail}"
                )
            tools = result.tools
        entry["tools"] = tools

        # The token is stored only once the server is known to work, so a failed
        # registration leaves nothing behind in the keyring.
        #
        # Registry first, keyring second — see the same ordering and reasoning in
        # backend.features.system.models.add_model. A crash between the two must
        # leave a visible server, not an unreachable secret.
        if request.token:
            entry["key_ref"] = store.key_ref_for(name)

        servers = store.load_servers(settings.mcp_servers_file)
        servers.append({k: v for k, v in entry.items() if v not in (None, [], {})})
        store.save_servers(settings.mcp_servers_file, servers)

        if request.token:
            try:
                store_key(entry["key_ref"], request.token)
            except CredentialError as exc:
                store.save_servers(
                    settings.mcp_servers_file,
                    [
                        e
                        for e in store.load_servers(settings.mcp_servers_file)
                        if e.get("name") != name
                    ],
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        return next(server for server in _servers() if server.name == name)

    @router.delete("/integrations/mcp/{name}", response_model=ConnectResult)
    def delete_mcp_server(name: str) -> ConnectResult:
        servers = store.load_servers(settings.mcp_servers_file)
        target = next((entry for entry in servers if entry.get("name") == name), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"no server {name}")
        # An orphaned secret would outlive the thing it belonged to, so the
        # keyring goes first: a crash after this leaves a listed server the user
        # can delete again, not a secret nothing points at.
        delete_key(target.get("key_ref"))
        store.save_servers(
            settings.mcp_servers_file, [e for e in servers if e.get("name") != name]
        )
        return ConnectResult(ok=True, detail=f"{name} removed")

    @router.get("/integrations/mcp/snippets", response_model=dict[str, str])
    def mcp_snippets() -> dict[str, str]:
        """Copy-paste config exposing *Argus* to your coding agents.

        The other direction from the registry above, and the one thing the old
        static panel gestured at without ever serving.
        """
        from backend.agent.mcp_server import client_config_snippets

        return client_config_snippets()

    return router
