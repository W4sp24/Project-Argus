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

import sys
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
from backend.connectors import client_library_importable, gcal, todoist
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
    #: "wired" | "not-connected" | "needs-credentials" | "failing"
    status: str
    detail: str
    #: Whether the UI can offer a connect flow (vs an informational row).
    can_connect: bool = True
    #: Set only when status == "failing" — why the stored credential can't
    #: actually be used right now.
    error: str | None = None


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
        """The uploaded client file, falling back to the legacy env-anchored one.

        New uploads land in the vault's ``.argus/`` dir
        (`Settings.gcal_credentials_file`) — a packaged desktop install has no
        writable repo root to put them beside. The fallback,
        `Settings.gcal_legacy_credentials_file`, keeps anyone who already
        hand-placed a ``credentials.json`` next to the env file working.
        """
        uploaded = settings.gcal_credentials_file
        if uploaded.is_file():
            return uploaded
        return settings.gcal_legacy_credentials_file

    def _connectors() -> list[ConnectorInfo]:
        """Live connector state for the hub.

        "wired" today means only "a keyring entry exists," which is not the
        same as "actually works" — a build shipped without the client
        library still reports a token as connected. The only signal checked
        here for that is whether the client library imports: cheap and
        local, unlike a live round-trip to Todoist/Google, which this
        endpoint (fetched on every Integrations page load) must not do.
        """
        gcal_ready = gcal.configured()
        has_client = _credentials_path().is_file()
        gcal_failing = gcal_ready and not client_library_importable("google_auth_oauthlib.flow")
        gcal_error = (
            "this build shipped without Google Calendar support — update Argus"
            if gcal_failing
            else None
        )

        todoist_ready = todoist.configured()
        todoist_failing = todoist_ready and not client_library_importable(
            "todoist_api_python.api"
        )
        todoist_error = (
            "this build shipped without Todoist support — update Argus"
            if todoist_failing
            else None
        )

        return [
            ConnectorInfo(
                id="gcal",
                name="Google Calendar",
                status=(
                    "failing"
                    if gcal_failing
                    else "wired"
                    if gcal_ready
                    else ("not-connected" if has_client else "needs-credentials")
                ),
                detail=(
                    gcal_error
                    or (
                        "merged into PLANNER.TIMELINE"
                        if gcal_ready
                        else (
                            "OAuth client saved — finish the browser consent to connect"
                            if has_client
                            else "needs a Desktop OAuth client from Google Cloud Console"
                        )
                    )
                ),
                error=gcal_error,
            ),
            ConnectorInfo(
                id="todoist",
                name="Todoist",
                status=(
                    "failing"
                    if todoist_failing
                    else ("wired" if todoist_ready else "not-connected")
                ),
                detail=(
                    todoist_error
                    or ("merged into TASKS.DUE" if todoist_ready else "needs a personal API token")
                ),
                error=todoist_error,
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

        try:
            from todoist_api_python.api import TodoistAPI
        except ImportError as exc:
            # todoist-api-python is a hard dependency now — an ImportError here
            # means this build is broken, not that the client is an optional
            # extra. Storing the token anyway is exactly what let a broken
            # build report success and then take the agenda, task board, and
            # planner down the moment someone connected. Mirrors the gcal 501
            # below: a frozen build can't `pip install` its way out, so only
            # the dev/source case gets that advice.
            if getattr(sys, "frozen", False):
                detail = (
                    "This build of Argus shipped without Todoist support "
                    "— update Argus to a version that includes it."
                )
            else:
                detail = (
                    f"Todoist support is not installed in this build ({exc}). "
                    "Install the todoist-api-python dependency."
                )
            raise HTTPException(status_code=501, detail=detail) from exc

        try:
            todoist.list_tasks(TodoistAPI(token))
        except Exception as exc:  # noqa: BLE001 - a rejected token is normal here
            raise HTTPException(
                status_code=422, detail=f"Todoist rejected that token: {exc}"
            ) from exc

        todoist.connect(token)
        return ConnectResult(ok=True, detail="Todoist connected and verified")

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
            # A frozen build has no pip and no source tree — "install the
            # extra" is not something that user can act on. Only the
            # dev/source case, where it IS actionable, gets the pip advice.
            if getattr(sys, "frozen", False):
                detail = (
                    "This build of Argus shipped without Google Calendar support "
                    "— update Argus to a version that includes it."
                )
            else:
                detail = (
                    "Google Calendar support is not installed in this build "
                    f"({exc}). Install the google-auth-oauthlib extra."
                )
            raise HTTPException(status_code=501, detail=detail) from exc
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
