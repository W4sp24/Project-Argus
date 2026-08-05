"""AUTOMATIONS endpoints — register an n8n instance, discover its workflows,
and run them from a dashboard card.

Modelled directly on :mod:`backend.features.integrations.router` (the
established register -> probe -> list -> delete pattern, the injectable
prober/client seam, and above all the keyring ordering rule — see
:func:`register_instance` and :func:`delete_instance_route` below, which
restate that reasoning in their own comments) and
:mod:`backend.features.system.router` (the local ``db()`` helper).

The one piece of genuinely new logic here is run dispatch (§ "Run dispatch"
below): a workflow's *response shape* — not any extra configuration — tells
Argus whether the run was a bare acknowledgement, a status message, or a
dashboard-widget push, and a workflow tagged ``argus:async`` is not awaited
at all. See :func:`_dispatch_result` and :func:`run_workflow`.

Nothing here talks to :mod:`backend.features.external` or
:mod:`backend.vault.privacy` — those are another workstream's surface. The
``argus:async`` result this router fires and forgets is *resolved* over
there, via the inbound ``?run={id}`` push; this module only issues the run id
and gets the trigger POST out the door.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.agent.credentials import (
    KEY_PRESENT,
    CredentialError,
    delete_key,
    get_key,
    key_state,
    store_key,
)
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.automations.n8n_client import (
    N8nClient,
    N8nError,
    N8nProbeResult,
    N8nTimeout,
    fire,
    form_url,
    webhook_url,
)
from backend.features.automations.n8n_client import probe as n8n_probe
from backend.features.automations.schema import (
    AutomationSchema,
    WidgetValidationError,
    is_async_workflow,
    parse_workflow,
    requires_confirmation,
    validate_widget_payload,
)

#: The synchronous run budget. n8n owns the workflow either way — this bounds
#: only how long Argus's HTTP layer waits for a response before reporting
#: TIMED OUT and moving on; it never cancels anything on n8n's side.
RUN_TIMEOUT_SECONDS = 30.0

#: The tag whose presence routes discovery/refresh — the entire registration
#: mechanism for a workflow becoming an Argus card is carrying this tag.
DISCOVERY_TAG = "argus"

ClientFactory = Callable[[dict[str, Any], str], N8nClient]


def _default_now() -> datetime:
    return datetime.now(UTC)


def _default_client_factory(instance: dict[str, Any], api_key: str) -> N8nClient:
    return N8nClient(base_url=instance["base_url"], api_key=api_key)


# --- response/request models -------------------------------------------------


class InstanceInfo(BaseModel):
    """The single registered n8n instance. Never carries its API key (I4) —
    only ``has_key``/``key_state``, exactly like ``McpServerInfo``."""

    name: str
    base_url: str
    has_key: bool = False
    key_state: str = "absent"


class InstanceRequest(BaseModel):
    name: str
    base_url: str
    api_key: str


class ProbeRequest(BaseModel):
    base_url: str
    api_key: str


class ProbeResultOut(BaseModel):
    """Mirrors :class:`N8nProbeResult` as a response model."""

    ok: bool
    detail: str
    latency_ms: int | None = None
    workflow_count: int | None = None

    @classmethod
    def from_result(cls, result: N8nProbeResult) -> ProbeResultOut:
        return cls(
            ok=result.ok,
            detail=result.detail,
            latency_ms=result.latency_ms,
            workflow_count=result.workflow_count,
        )


class ConnectResult(BaseModel):
    ok: bool
    detail: str


class RunOut(BaseModel):
    """One row of ``automation_runs``, as returned to the client."""

    id: str
    workflow_id: str
    workflow_name: str | None = None
    started_at: str
    finished_at: str | None = None
    status: str
    mode: str | None = None
    message: str | None = None
    execution_id: str | None = None
    payload: Any | None = None


class WorkflowCard(BaseModel):
    """One cached workflow, parsed for the dashboard."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str | None = None
    tags: list[str] = []
    #: "form" | "button" | "none" — see schema.parse_workflow.
    kind: str
    fields: list[dict[str, Any]] = []
    webhook_id: str | None = None
    webhook_path: str | None = None
    basic_auth: bool = False
    #: argus:confirm — surfaced as a flag only. The UI gates firing behind a
    #: ConfirmDialog; the server does NOT prompt or block on this itself, so
    #: nobody mistakes this for an enforced server-side check.
    confirm: bool = False
    #: argus:async, aliased to "async" on the wire since `async` is a Python
    #: keyword and cannot be a field name.
    is_async: bool = Field(default=False, alias="async")
    active: bool = False
    last_seen_at: str
    last_run: RunOut | None = None


class AutomationsResponse(BaseModel):
    instance: InstanceInfo | None
    workflows: list[WorkflowCard]
    #: Whether n8n answered the live reachability check made on this request.
    #: False (with cached cards still populated) is a normal, fully-supported
    #: response — the dashboard never blocks on n8n and never goes blank
    #: because a second service is down.
    connected: bool
    detail: str


class RefreshResult(BaseModel):
    ok: bool
    count: int
    dropped: int


class RunRequest(BaseModel):
    """Form field values / arbitrary body forwarded verbatim to n8n."""

    payload: dict[str, Any] = Field(default_factory=dict)


class RunResponse(BaseModel):
    run_id: str
    #: "running" | "ok" | "failed" | "timeout"
    status: str
    #: "ack" | "status" | "widget" | None (still-running async, or a
    #: transport-level failure that never got far enough to have a mode).
    mode: str | None = None
    message: str | None = None
    execution_id: str | None = None
    execution_url: str | None = None
    payload: Any | None = None


class WidgetOut(BaseModel):
    slug: str
    title: str | None = None
    kind: str
    payload: Any
    last_seen_at: str | None = None
    expected_interval_seconds: int | None = None
    created_at: str
    position: int | None = None
    pinned: bool = False
    hidden: bool = False
    #: Computed, not stored — see store.widget_state.
    state: str


class WidgetPatchRequest(BaseModel):
    pinned: bool | None = None
    hidden: bool | None = None
    position: int | None = None


# --- small pure helpers -------------------------------------------------


def _tag_names(workflow: dict[str, Any]) -> list[str]:
    """Tag names off a raw n8n workflow dict, tolerant of both tag shapes.

    Mirrors ``schema._tag_names`` (private to that module) for the one extra
    thing this router needs beyond ``has_tag``: the full list, to cache
    verbatim in ``automation_workflows.tags`` for display.
    """
    tags = workflow.get("tags")
    if not isinstance(tags, list):
        return []
    names: list[str] = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            names.append(str(tag["name"]))
        elif isinstance(tag, str):
            names.append(tag)
    return names


def _error_message(body: Any) -> str | None:
    """A readable one-liner out of a (possibly error) response body."""
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if isinstance(message, str) and message:
            return message
    if isinstance(body, str) and body.strip():
        return body.strip()[:200]
    return None


def _trigger_url(base_url: str, parsed: AutomationSchema) -> str:
    """The URL to POST to fire ``parsed``'s trigger, or raise 422 if it can't be built."""
    if parsed.kind == "form":
        if not parsed.webhook_id:
            raise HTTPException(
                status_code=422,
                detail="this workflow's Form Trigger has no webhookId — re-save it in n8n",
            )
        return form_url(base_url, parsed.webhook_id)
    if parsed.kind == "button":
        if not parsed.webhook_path:
            raise HTTPException(
                status_code=422,
                detail="this workflow's Webhook Trigger has no path — re-save it in n8n",
            )
        return webhook_url(base_url, parsed.webhook_path)
    raise HTTPException(status_code=422, detail="this workflow has no runnable trigger")


def build_automations_router(
    settings: Settings,
    *,
    client_factory: ClientFactory | None = None,
    now: Callable[[], datetime] = _default_now,
) -> APIRouter:
    """All ``/api/automations`` routes.

    ``client_factory`` builds an :class:`N8nClient` from the registered
    instance dict (at minimum ``base_url``) plus a resolved API key —
    injectable so tests never open a socket or touch the real keyring. The
    default builds a plain client with no injected transport, exactly like
    ``build_integrations_router``'s default prober hits the real network.
    """
    router = APIRouter(prefix="/api")
    factory = client_factory or _default_client_factory

    # Per-process, per-router-instance guard against a double-click firing the
    # same workflow twice concurrently (e.g. two emails sent for one click).
    # This is deliberately an in-memory set, not a database query: Argus is a
    # single-user desktop app with exactly one backend process, so "per
    # process" is the whole deployment — there is no second process for a
    # second click to race against.
    inflight: set[str] = set()
    # Keeps references to fire-and-forget (argus:async) tasks alive; asyncio
    # does not guarantee a task survives if nothing holds a reference to it.
    background_tasks: set[asyncio.Task] = set()

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    def _instance_info(entry: dict[str, Any]) -> InstanceInfo:
        state = key_state(entry.get("key_ref"))
        return InstanceInfo(
            name=entry["name"],
            base_url=entry["base_url"],
            has_key=state == KEY_PRESENT,
            key_state=state,
        )

    def _require_instance() -> dict[str, Any]:
        instance = store.load_instance(settings.automations_file)
        if instance is None:
            raise HTTPException(status_code=409, detail="no n8n instance registered")
        return instance

    def _resolve_key(instance: dict[str, Any]) -> str:
        key = get_key(instance.get("key_ref"))
        if key is None:
            raise HTTPException(
                status_code=500,
                detail="n8n instance is registered but its API key is missing from the keyring",
            )
        return key

    async def _probe(base_url: str, api_key: str) -> N8nProbeResult:
        """Route probing through the same injectable transport as ``factory``.

        ``n8n_client.probe`` is a standalone function (it builds its own
        ``httpx.AsyncClient``, not an ``N8nClient``), so the seam here is
        borrowing the transport off a client the *same* injected factory
        builds — tests get one seam to control, not two.
        """
        client = factory({"base_url": base_url}, api_key)
        return await n8n_probe(base_url, api_key, transport=client.transport)

    def _card_from_row(conn: sqlite3.Connection, row: dict[str, Any]) -> WorkflowCard:
        definition = row["schema_json"] or {}
        parsed = parse_workflow(definition)
        recent = store.recent_runs_for(conn, row["id"], limit=1)
        last_run = RunOut(**recent[0]) if recent else None
        return WorkflowCard(
            id=row["id"],
            name=row["name"],
            tags=row["tags"],
            kind=parsed.kind,
            fields=[dataclasses.asdict(field) for field in parsed.fields],
            webhook_id=parsed.webhook_id,
            webhook_path=parsed.webhook_path,
            basic_auth=parsed.basic_auth,
            confirm=requires_confirmation(definition),
            is_async=is_async_workflow(definition),
            active=row["active"],
            last_seen_at=row["last_seen_at"],
            last_run=last_run,
        )

    # --- instance registry --------------------------------------------------

    @router.post("/automations/instance/test", response_model=ProbeResultOut)
    async def test_instance(request: ProbeRequest) -> ProbeResultOut:
        """Probe only — persists nothing, registered or not."""
        result = await _probe(request.base_url.strip(), request.api_key.strip())
        return ProbeResultOut.from_result(result)

    @router.post("/automations/instance", response_model=InstanceInfo, status_code=201)
    async def register_instance(request: InstanceRequest) -> InstanceInfo:
        name = request.name.strip()
        base_url = request.base_url.strip()
        api_key = request.api_key.strip()

        if not store.INSTANCE_NAME_RE.match(name):
            raise HTTPException(status_code=422, detail="invalid instance name")
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="base_url must be an http(s) URL")
        if not api_key:
            raise HTTPException(status_code=422, detail="api_key is required")

        existing = store.load_instance(settings.automations_file)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"an n8n instance is already registered ({existing['name']})",
            )

        # Verification before persistence: storing a base_url/key that turns
        # out to be wrong means the failure surfaces during the next refresh
        # instead of while the user is looking at the dialog they typed it
        # into. See build_integrations_router's module docstring.
        probe_result = await _probe(base_url, api_key)
        if not probe_result.ok:
            raise HTTPException(
                status_code=422, detail=f"could not connect to n8n: {probe_result.detail}"
            )

        # Registry row before the keyring secret, with rollback on failure.
        # Mirrors add_mcp_server's ordering exactly, and for the same reason:
        # a crash between the two writes must leave a visible instance the
        # user can see and retry, never an orphaned keyring secret nothing in
        # the registry points at.
        key_ref = store.key_ref_for(name)
        entry = {"name": name, "base_url": base_url, "key_ref": key_ref}
        store.save_instance(settings.automations_file, entry)
        try:
            store_key(key_ref, api_key)
        except CredentialError as exc:
            store.delete_instance(settings.automations_file)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return _instance_info(entry)

    @router.delete("/automations/instance", response_model=ConnectResult)
    def delete_instance_route() -> ConnectResult:
        instance = store.load_instance(settings.automations_file)
        if instance is None:
            raise HTTPException(status_code=404, detail="no n8n instance registered")

        # Keyring first, then registry: an orphaned secret would outlive the
        # thing it belonged to. A crash after this leaves a listed instance
        # the user can delete again, not a secret nothing points at anymore.
        # Mirrors delete_mcp_server exactly.
        delete_key(instance.get("key_ref"))
        store.delete_instance(settings.automations_file)

        conn = db()
        try:
            store.delete_missing_workflows(conn, [])
        finally:
            conn.close()
        return ConnectResult(ok=True, detail=f"{instance['name']} disconnected")

    # --- discovery -----------------------------------------------------------

    @router.get("/automations", response_model=AutomationsResponse)
    async def list_automations() -> AutomationsResponse:
        instance = store.load_instance(settings.automations_file)
        if instance is None:
            return AutomationsResponse(
                instance=None, workflows=[], connected=False, detail="no n8n instance registered"
            )

        info = _instance_info(instance)
        conn = db()
        try:
            cards = [_card_from_row(conn, row) for row in store.list_workflows(conn)]
        finally:
            conn.close()

        connected = False
        detail = "n8n instance is registered but has no stored API key"
        key = get_key(instance.get("key_ref"))
        if key is not None:
            client = factory(instance, key)
            # A live reachability check, but never a blocking one: the cards
            # above already came from cache regardless of what happens here.
            # A down n8n degrades this endpoint to connected: false, not to a
            # slow, empty, or failed dashboard load.
            try:
                await client.list_workflows(tags=DISCOVERY_TAG)
                connected = True
                detail = "connected"
            except N8nError as exc:
                connected = False
                detail = str(exc)

        return AutomationsResponse(
            instance=info, workflows=cards, connected=connected, detail=detail
        )

    @router.post("/automations/refresh", response_model=RefreshResult)
    async def refresh() -> RefreshResult:
        instance = _require_instance()
        api_key = _resolve_key(instance)
        client = factory(instance, api_key)
        try:
            workflows = await client.list_workflows(tags=DISCOVERY_TAG)
        except N8nError as exc:
            raise HTTPException(
                status_code=502, detail=f"could not refresh from n8n: {exc}"
            ) from exc

        conn = db()
        try:
            seen_ids: list[str] = []
            for workflow in workflows:
                raw_id = workflow.get("id")
                if raw_id is None:
                    continue
                workflow_id = str(raw_id)
                seen_ids.append(workflow_id)
                name = workflow.get("name")
                store.upsert_workflow(
                    conn,
                    workflow_id,
                    name=str(name) if name else None,
                    tags=_tag_names(workflow),
                    schema_json=workflow,
                    active=bool(workflow.get("active", False)),
                    now=now,
                )
            # Untagging a workflow in n8n (or deleting it) means it drops out
            # of this response — anything cached but not seen this pass loses
            # its card.
            dropped = store.delete_missing_workflows(conn, seen_ids)
        finally:
            conn.close()

        return RefreshResult(ok=True, count=len(seen_ids), dropped=dropped)

    # --- run dispatch ---------------------------------------------------------

    def _finish_failed(
        conn: sqlite3.Connection,
        run_id: str,
        *,
        status: str,
        mode: str | None,
        message: str | None,
        execution_id: str | None = None,
    ) -> None:
        store.finish_run(
            conn, run_id, status, mode=mode, message=message, execution_id=execution_id, now=now
        )

    def _dispatch_result(
        conn: sqlite3.Connection,
        run_id: str,
        client: N8nClient,
        result: Any,
    ) -> RunResponse:
        """Turn a completed :class:`N8nRunResult` into a finished run + response.

        The response *shape* declares the mode, with no extra configuration:
        see the module docstring's dispatch table. This is the one place that
        table is implemented.
        """
        execution_id = result.execution_id
        link = client.execution_url(execution_id) if execution_id else None

        if result.status_code >= 400:
            message = _error_message(result.body) or f"n8n returned {result.status_code}"
            _finish_failed(
                conn, run_id, status="failed", mode=None, message=message,
                execution_id=execution_id,
            )
            return RunResponse(
                run_id=run_id, status="failed", mode=None, message=message,
                execution_id=execution_id, execution_url=link,
            )

        body = result.body

        if isinstance(body, dict) and "widget" in body:
            slug = body.get("slug")
            if not isinstance(slug, str) or not slug:
                message = "widget response is missing a 'slug'"
                _finish_failed(
                    conn, run_id, status="failed", mode="widget", message=message,
                    execution_id=execution_id,
                )
                return RunResponse(
                    run_id=run_id, status="failed", mode="widget", message=message,
                    execution_id=execution_id, execution_url=link,
                )
            try:
                validated = validate_widget_payload(body)
            except WidgetValidationError as exc:
                # The previously stored payload for this slug (if any) is left
                # exactly as it was — a broken push must never overwrite good
                # data on the dashboard.
                _finish_failed(
                    conn, run_id, status="failed", mode="widget", message=str(exc),
                    execution_id=execution_id,
                )
                return RunResponse(
                    run_id=run_id, status="failed", mode="widget", message=str(exc),
                    execution_id=execution_id, execution_url=link,
                )
            store.upsert_widget(
                conn, slug, validated.kind, validated.payload,
                title=validated.title,
                expected_interval_seconds=validated.expected_interval_seconds,
                now=now,
            )
            store.finish_run(
                conn, run_id, "ok", mode="widget", payload=validated.payload,
                execution_id=execution_id, now=now,
            )
            return RunResponse(
                run_id=run_id, status="ok", mode="widget", payload=validated.payload,
                execution_id=execution_id, execution_url=link,
            )

        if isinstance(body, dict) and "ok" in body:
            ok = bool(body.get("ok"))
            message = body.get("message")
            status = "ok" if ok else "failed"
            store.finish_run(
                conn, run_id, status, mode="status", message=message,
                execution_id=execution_id, now=now,
            )
            return RunResponse(
                run_id=run_id, status=status, mode="status", message=message,
                execution_id=execution_id, execution_url=link,
            )

        # Empty body, or a 2xx JSON shape this router doesn't recognise —
        # both degrade to a bare acknowledgement rather than an error: the
        # trigger plainly succeeded (2xx), it just didn't say anything Argus
        # knows how to interpret further.
        store.finish_run(conn, run_id, "ok", mode="ack", execution_id=execution_id, now=now)
        return RunResponse(
            run_id=run_id, status="ok", mode="ack", execution_id=execution_id, execution_url=link
        )

    async def _fire_async(run_id: str, url: str, payload: dict[str, Any], transport: Any) -> None:
        """The argus:async path's background half: get the trigger POST out
        the door, then step out of the way.

        Deliberately does NOT call ``finish_run`` on a successful fire: the
        real outcome of a multi-step async workflow arrives later via the
        external ``?run={id}`` push (a different workstream's router), and
        this run stays ``running`` until that push resolves it. The only
        thing worth recording from here is an outright failure to even
        deliver the trigger — if that happens, no push will ever arrive to
        resolve this run, so leaving it "running" forever would be a lie.
        """
        conn = db()
        try:
            try:
                await fire(url, payload, transport=transport)
            except N8nTimeout as exc:
                _finish_failed(conn, run_id, status="timeout", mode=None, message=str(exc))
            except N8nError as exc:
                _finish_failed(conn, run_id, status="failed", mode=None, message=str(exc))
        finally:
            conn.close()

    @router.post("/automations/{workflow_id}/run", response_model=RunResponse)
    async def run_workflow(workflow_id: str, request: RunRequest) -> RunResponse:
        instance = _require_instance()
        api_key = _resolve_key(instance)

        conn = db()
        try:
            row = store.get_workflow(conn, workflow_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no known workflow {workflow_id}")

            definition = row["schema_json"] or {}
            parsed = parse_workflow(definition)
            url = _trigger_url(instance["base_url"], parsed)

            if workflow_id in inflight:
                raise HTTPException(
                    status_code=409,
                    detail=f"{workflow_id} already has a run in flight",
                )
            inflight.add(workflow_id)

            run_id = str(uuid.uuid4())
            store.record_run_started(conn, run_id, workflow_id, workflow_name=row["name"], now=now)

            client = factory(instance, api_key)
            transport = client.transport

            if is_async_workflow(definition):
                task = asyncio.create_task(_fire_async(run_id, url, request.payload, transport))
                background_tasks.add(task)

                def _on_done(t: asyncio.Task, _workflow_id: str = workflow_id) -> None:
                    background_tasks.discard(t)
                    inflight.discard(_workflow_id)

                task.add_done_callback(_on_done)
                return RunResponse(run_id=run_id, status="running", mode=None)

            try:
                try:
                    result = await fire(
                        url, request.payload, timeout=RUN_TIMEOUT_SECONDS, transport=transport
                    )
                except N8nTimeout as exc:
                    # A client-side timeout means no response ever arrived, so
                    # there is no execution id to link to — n8n still owns
                    # (and is still running) the workflow; this call is not
                    # cancelling anything, only giving up on waiting for it.
                    _finish_failed(conn, run_id, status="timeout", mode=None, message=str(exc))
                    return RunResponse(run_id=run_id, status="timeout", mode=None, message=str(exc))
                except N8nError as exc:
                    _finish_failed(conn, run_id, status="failed", mode=None, message=str(exc))
                    return RunResponse(run_id=run_id, status="failed", mode=None, message=str(exc))
            finally:
                inflight.discard(workflow_id)

            return _dispatch_result(conn, run_id, client, result)
        finally:
            conn.close()

    # --- run history -----------------------------------------------------------

    @router.get("/automations/runs", response_model=list[RunOut])
    def list_runs_route(limit: int = 50, workflow_id: str | None = None) -> list[RunOut]:
        conn = db()
        try:
            return [
                RunOut(**row) for row in store.list_runs(conn, limit=limit, workflow_id=workflow_id)
            ]
        finally:
            conn.close()

    # --- widgets ---------------------------------------------------------------

    @router.get("/automations/widgets", response_model=list[WidgetOut])
    def list_widgets_route(include_hidden: bool = False) -> list[WidgetOut]:
        conn = db()
        try:
            rows = store.list_widgets(conn, include_hidden=include_hidden)
            return [WidgetOut(**row, state=store.widget_state(row, now=now)) for row in rows]
        finally:
            conn.close()

    @router.patch("/automations/widgets/{slug}", response_model=WidgetOut)
    def patch_widget(slug: str, request: WidgetPatchRequest) -> WidgetOut:
        conn = db()
        try:
            existing = store.get_widget(conn, slug)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no widget {slug}")

            store.set_widget_flags(
                conn, slug, pinned=request.pinned, hidden=request.hidden, position=request.position
            )
            if request.pinned is not None or request.hidden is not None or (
                request.position is not None
            ):
                # Any manual pin/hide/reorder is the user taking control of the
                # dashboard layout — an auto-layout pass must not silently
                # reshuffle a card the user just placed by hand.
                store.set_pref(conn, "layout_taken_control", "true")

            row = store.get_widget(conn, slug)
            assert row is not None  # just confirmed above
            return WidgetOut(**row, state=store.widget_state(row, now=now))
        finally:
            conn.close()

    @router.delete("/automations/widgets/{slug}", response_model=ConnectResult)
    def delete_widget_route(slug: str) -> ConnectResult:
        conn = db()
        try:
            existing = store.get_widget(conn, slug)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no widget {slug}")
            store.delete_widget(conn, slug)
        finally:
            conn.close()
        return ConnectResult(ok=True, detail=f"{slug} removed")

    return router


__all__ = ["build_automations_router"]
