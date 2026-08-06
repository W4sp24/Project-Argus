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
import logging
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
from backend.features.automations import catalog, sources, store
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
    has_tag,
    is_async_workflow,
    parse_workflow,
    requires_confirmation,
    validate_widget_payload,
)
from backend.features.external import auth as external_auth

logger = logging.getLogger(__name__)

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
    """One registered n8n instance. Never carries its API key (I4) —
    only ``has_key``/``key_state``, exactly like ``McpServerInfo``."""

    id: str
    name: str
    #: "LOCAL" | "REMOTE".
    kind: str = "REMOTE"
    base_url: str
    has_key: bool = False
    key_state: str = "absent"
    #: Live reachability. Defaults True (a fresh registration was just probed
    #: ok); GET /automations/instances overrides this with a real, concurrent
    #: probe per instance — see list_instances/_reachability below.
    connected: bool = True


class InstanceRequest(BaseModel):
    name: str
    base_url: str
    api_key: str
    kind: str = "REMOTE"


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


class DiscoverRequest(BaseModel):
    """Same shape as :class:`ProbeRequest` — a not-yet-registered instance's
    credentials, for the connect dialog's discover step."""

    base_url: str
    api_key: str


class DiscoveredWorkflowOut(BaseModel):
    """One ``argus``-tagged workflow on a not-yet-registered instance, shaped
    for a picker — see ``POST /automations/instances/discover``."""

    id: str
    name: str | None = None
    active: bool = False
    #: "action" when the trigger is something Argus can fire from a card (a
    #: form or a bare webhook button — see schema.parse_workflow's `kind`);
    #: "display" otherwise (schedule/cron/manual-triggered, so its only way
    #: of reaching Argus is pushing a widget on its own cadence).
    kind: str
    #: Whether the workflow actually carries the `argus` tag. Every entry
    #: this endpoint returns does — it only ever queries n8n for
    #: `?tags=argus` — so this is always `True` in practice; kept as an
    #: explicit field (rather than assumed) because it is what a picker
    #: checks, not an implementation detail the frontend should have to infer.
    tagged: bool


class RunOut(BaseModel):
    """One row of ``automation_runs``, as returned to the client."""

    id: str
    workflow_id: str
    workflow_name: str | None = None
    #: Additive (B3): defaults to ``""``, the "not yet attributed" sentinel,
    #: so a pre-multi-instance frontend that doesn't know this field exists
    #: keeps compiling and behaving identically.
    instance_id: str = ""
    started_at: str
    finished_at: str | None = None
    status: str
    mode: str | None = None
    message: str | None = None
    execution_id: str | None = None
    payload: Any | None = None


class SourceProvenance(BaseModel):
    """Which path is answering for each migratable source: "n8n" | "connector"."""

    calendar: str
    tasks: str


class EventOut(BaseModel):
    """One row of ``automation_events`` — the dashboard's activity feed."""

    ts: str
    instance_id: str = ""
    tag: str
    subject: str | None = None
    text: str


class WorkflowCard(BaseModel):
    """One cached workflow, parsed for the dashboard."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    #: Additive (B3) — see RunOut.instance_id.
    instance_id: str = ""
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
    #: F9 removes ``instance`` in favor of this. Until then it is derived
    #: from this list — ``instances[0] if len(instances) == 1 else None`` —
    #: so a pre-multi-instance frontend keeps rendering exactly what it
    #: always has for 0 or 1 registered instances, and degrades to its "no
    #: instance registered" empty state for 2+ rather than showing stale or
    #: wrong data.
    instances: list[InstanceInfo] = []
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
    #: For ``mode="widget"`` runs only: which widget the run wrote. The
    #: payload deliberately carries only the kind-specific fields (slug and
    #: kind are stripped by ``ValidatedWidget``), so without these the caller
    #: knows a widget was pushed but not *which* — and cannot act on it. The
    #: palette needs exactly this to offer "pin to dashboard" on a result.
    widget_slug: str | None = None
    widget_kind: str | None = None
    #: Which instance ran it. With several registered, "it worked" and "it
    #: worked somewhere else" are different answers.
    instance_id: str | None = None


class WidgetOut(BaseModel):
    slug: str
    #: Additive (B3) — see RunOut.instance_id.
    instance_id: str = ""
    title: str | None = None
    kind: str
    payload: Any
    last_seen_at: str | None = None
    expected_interval_seconds: int | None = None
    created_at: str
    position: int | None = None
    pinned: bool = False
    hidden: bool = False
    #: Dashboard grid footprint, in cells (1..4 each). The renderer proposes a
    #: default; the user's own resize overrides it.
    grid_cols: int = 1
    grid_rows: int = 1
    #: Whether the user has taken control of *this* widget's placement.
    #: Per-widget, not global: dragging one card must not freeze the rest, or
    #: installing a new automation would stop being zero-configuration.
    layout_locked: bool = False
    #: Computed, not stored — see store.widget_state.
    state: str


class WidgetPatchRequest(BaseModel):
    pinned: bool | None = None
    hidden: bool | None = None
    position: int | None = None
    grid_cols: int | None = None
    grid_rows: int | None = None


class TemplateOut(BaseModel):
    """Mirrors :class:`backend.features.automations.catalog.Template`, plus
    ``installed`` — computed here, not stored on the catalog entry itself,
    since "installed" is a fact about the *cached workflow list*, not about
    the template."""

    id: str
    name: str
    description: str
    kind: str
    widget_slug: str | None = None
    replaces: str | None = None
    requires: list[str] = []
    #: Short factual badges (renderer, field count, cadence), derived from the
    #: bundled definition — see catalog.Template.chips.
    chips: list[str] = []
    installed: bool = False

    @classmethod
    def from_template(cls, template: catalog.Template, *, installed: bool) -> TemplateOut:
        return cls(
            id=template.id,
            name=template.name,
            description=template.description,
            kind=template.kind,
            widget_slug=template.widget_slug,
            replaces=template.replaces,
            requires=list(template.requires),
            chips=list(template.chips),
            installed=installed,
        )


class ExternalSurfaceInfo(BaseModel):
    """The inbound surface's configuration, minus its secret."""

    enabled: bool
    port: int
    #: The public URL the user's tunnel forwards from. Empty until configured,
    #: and templates cannot be installed while it is, because a workflow
    #: posting to nowhere fails silently later.
    base_url: str
    #: Tri-state, as everywhere else a key is reported: present / absent /
    #: unknown. "unknown" means the keyring could not be read, which must
    #: never be shown as "no token".
    token_state: str


class ExternalTokenResult(BaseModel):
    """A freshly issued token — the only response that ever carries its value."""

    token: str
    rotated: bool
    header_name: str
    header_value: str
    base_url: str


class InstallResult(BaseModel):
    workflow_id: str
    #: Deep link to the newly-created workflow in n8n's own UI — where the
    #: user grants the credential this template `requires`. That hand-off is
    #: the one genuinely-can't-automate-it step: OAuth needs a real browser
    #: round trip, and doing the equivalent for an API-key credential type
    #: would mean handing Argus the third-party secret again, which is the
    #: exact thing this whole n8n migration exists to stop.
    open_in_n8n: str


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
        # B3: cheap (a read, once nothing is left to backfill — see the
        # function's own docstring) and idempotent per connection, the same
        # guard-then-noop idiom init_schema's own migrations use above.
        store.ensure_instance_attribution(conn, settings.automations_file)
        # B6: one-time fold of the old global layout pref into per-widget
        # layout_locked. Same guard-then-noop shape — the pref is deleted
        # once folded, so this is a single indexed read from then on.
        store.migrate_global_layout_pref(conn)
        return conn

    def _instance_info(entry: dict[str, Any], *, connected: bool = True) -> InstanceInfo:
        state = key_state(entry.get("key_ref"))
        return InstanceInfo(
            id=entry["id"],
            name=entry["name"],
            kind=entry.get("kind", "REMOTE"),
            base_url=entry["base_url"],
            has_key=state == KEY_PRESENT,
            key_state=state,
            connected=connected,
        )

    def _resolve_instance(instance_id: str) -> dict[str, Any]:
        """The registered instance at ``instance_id``, or 404."""
        instances = store.load_instances(settings.automations_file)
        entry = next((e for e in instances if e.get("id") == instance_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no n8n instance {instance_id}")
        return entry

    def _only_instance() -> dict[str, Any]:
        """The sole registered instance — 409 unless there is *exactly* one.

        B3 narrowed this from the old ``_require_instance`` (which used to
        return ``instances[0]`` for 2+ registered, silently picking one).
        Used only by routes that are genuinely "the one instance" shims —
        today, ``POST /automations/templates/{id}/install`` — never by
        anything instance-aware, which resolves via ``_resolve_instance``
        (a path parameter) instead.
        """
        instances = store.load_instances(settings.automations_file)
        if not instances:
            raise HTTPException(status_code=409, detail="no n8n instance registered")
        if len(instances) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "multiple n8n instances registered — use "
                    "POST /automations/instances/{instance_id}/templates/{template_id}/install"
                ),
            )
        return instances[0]

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

    async def _reachability(instance: dict[str, Any]) -> tuple[bool, str]:
        """A live, non-blocking reachability check for one registered instance.

        Never raises — a down n8n degrades to ``(False, detail)``, never to a
        slow, empty, or failed response. Shared by ``GET /automations`` (the
        single-instance view) and ``GET /automations/instances`` (every
        instance, probed concurrently — see ``list_instances``).
        """
        key = get_key(instance.get("key_ref"))
        if key is None:
            return False, "n8n instance is registered but has no stored API key"
        client = factory(instance, key)
        try:
            await client.list_workflows(tags=DISCOVERY_TAG)
            return True, "connected"
        except N8nError as exc:
            return False, str(exc)

    def _card_from_row(conn: sqlite3.Connection, row: dict[str, Any]) -> WorkflowCard:
        definition = row["schema_json"] or {}
        parsed = parse_workflow(definition)
        # Scoped to this row's own instance: workflow ids are only unique
        # within an instance, so an unscoped lookup could surface a
        # same-numbered but unrelated workflow's last run from a different
        # instance.
        recent = store.recent_runs_for(conn, row["id"], limit=1, instance_id=row["instance_id"])
        last_run = RunOut(**recent[0]) if recent else None
        return WorkflowCard(
            id=row["id"],
            instance_id=row["instance_id"],
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

    @router.post("/automations/instances/test", response_model=ProbeResultOut)
    @router.post("/automations/instance/test", response_model=ProbeResultOut)  # F9 removes this
    async def test_instance(request: ProbeRequest) -> ProbeResultOut:
        """Probe only — persists nothing, registered or not."""
        result = await _probe(request.base_url.strip(), request.api_key.strip())
        return ProbeResultOut.from_result(result)

    @router.post("/automations/instances", response_model=InstanceInfo, status_code=201)
    async def register_instance(request: InstanceRequest) -> InstanceInfo:
        name = request.name.strip()
        base_url = request.base_url.strip()
        api_key = request.api_key.strip()
        kind = request.kind.strip().upper() or "REMOTE"

        if not store.INSTANCE_NAME_RE.match(name):
            raise HTTPException(status_code=422, detail="invalid instance name")
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="base_url must be an http(s) URL")
        if not api_key:
            raise HTTPException(status_code=422, detail="api_key is required")
        if kind not in ("LOCAL", "REMOTE"):
            raise HTTPException(status_code=422, detail="kind must be 'LOCAL' or 'REMOTE'")

        instances = store.load_instances(settings.automations_file)
        # 409 means a duplicate NAME now, not "an instance already exists" —
        # registering a second, distinctly-named instance is the whole point
        # of this endpoint.
        if any(entry["name"] == name for entry in instances):
            raise HTTPException(
                status_code=409, detail=f"an n8n instance named {name!r} already exists"
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
        entry = {
            "id": uuid.uuid4().hex,
            "name": name,
            "kind": kind,
            "base_url": base_url,
            "key_ref": key_ref,
        }
        instances.append(entry)
        store.save_instances(settings.automations_file, instances)
        try:
            store_key(key_ref, api_key)
        except CredentialError as exc:
            # Roll back only the row just added — reload first, since another
            # request could have registered/removed a different instance
            # between the append above and this failure, and the other
            # instances' rows (and keyring secrets) must be untouched.
            remaining = [
                e
                for e in store.load_instances(settings.automations_file)
                if e["id"] != entry["id"]
            ]
            store.save_instances(settings.automations_file, remaining)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return _instance_info(entry)

    @router.post("/automations/instance", response_model=InstanceInfo, status_code=201)
    async def register_instance_compat(request: InstanceRequest) -> InstanceInfo:
        """Compat shim: 409 if the list is non-empty — identical observable
        behaviour to before multi-instance support for anyone who only ever
        registers one. F9 removes this in favor of POST /automations/instances."""
        existing = store.load_instances(settings.automations_file)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"an n8n instance is already registered ({existing[0]['name']})",
            )
        return await register_instance(request)

    @router.delete("/automations/instances/{instance_id}", response_model=ConnectResult)
    def delete_instance_by_id(instance_id: str) -> ConnectResult:
        instances = store.load_instances(settings.automations_file)
        target = next((entry for entry in instances if entry.get("id") == instance_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"no n8n instance {instance_id}")

        # Keyring first, then registry: an orphaned secret would outlive the
        # thing it belonged to. A crash after this leaves a listed instance
        # the user can delete again, not a secret nothing points at anymore.
        # Mirrors delete_mcp_server exactly. Only this instance's row is
        # dropped — the rest of the list (and their keyring secrets, never
        # touched here) is written back unchanged.
        delete_key(target.get("key_ref"))
        # B4: this instance's inbound external token lives under its own
        # keyring reference (external_auth.token_ref_for), separate from its
        # n8n API key above — deleting an instance must revoke both, and
        # must not touch any other instance's token (each is scoped by id).
        delete_key(external_auth.token_ref_for(instance_id))
        remaining = [entry for entry in instances if entry.get("id") != instance_id]
        store.save_instances(settings.automations_file, remaining)

        conn = db()
        try:
            # Scoped to this instance's real id: refresh/discovery writes
            # every cached workflow under its own instance's real id (see
            # _refresh_instance), so this clears exactly this instance's
            # cache and nothing else's.
            store.delete_missing_workflows(conn, [], instance_id=instance_id)
        finally:
            conn.close()
        return ConnectResult(ok=True, detail=f"{target['name']} disconnected")

    @router.delete("/automations/instance", response_model=ConnectResult)
    def delete_instance_route() -> ConnectResult:
        """Compat shim: deletes ``instances[0]``, 404 when empty — identical
        observable behaviour to before multi-instance support. F9 removes
        this in favor of DELETE /automations/instances/{id}."""
        instances = store.load_instances(settings.automations_file)
        if not instances:
            raise HTTPException(status_code=404, detail="no n8n instance registered")
        target = instances[0]

        # Keyring first, then registry — same ordering and reasoning as
        # delete_instance_by_id above.
        delete_key(target.get("key_ref"))
        delete_key(external_auth.token_ref_for(target["id"]))
        store.save_instances(settings.automations_file, instances[1:])

        conn = db()
        try:
            store.delete_missing_workflows(conn, [])
        finally:
            conn.close()
        return ConnectResult(ok=True, detail=f"{target['name']} disconnected")

    # --- pre-registration discovery ------------------------------------------
    #
    # B3: the connect dialog's discover step. Deliberately independent of the
    # registry: it must work on a base_url/api_key pair the user just typed,
    # before anything is persisted, so the picker can show which workflows
    # exist before the user commits to registering the instance at all.

    @router.post("/automations/instances/discover", response_model=list[DiscoveredWorkflowOut])
    async def discover_instance_workflows(request: DiscoverRequest) -> list[DiscoveredWorkflowOut]:
        """The ``argus``-tagged workflows on a not-yet-registered instance.

        Persists nothing — no sqlite write, no registry write — and does not
        require (or check for) an existing registration; it exists so the
        connect dialog can show the user what they're about to register
        *before* they commit to it. Reuses ``schema.parse_workflow``/
        ``schema.has_tag`` (the same parsing every cached-workflow card goes
        through) rather than duplicating either.
        """
        base_url = request.base_url.strip()
        api_key = request.api_key.strip()
        client = factory({"base_url": base_url}, api_key)
        try:
            workflows = await client.list_workflows(tags=DISCOVERY_TAG)
        except N8nError as exc:
            raise HTTPException(
                status_code=502, detail=f"could not reach n8n: {exc}"
            ) from exc

        results: list[DiscoveredWorkflowOut] = []
        for workflow in workflows:
            raw_id = workflow.get("id")
            if raw_id is None:
                continue
            parsed = parse_workflow(workflow)
            name = workflow.get("name")
            results.append(
                DiscoveredWorkflowOut(
                    id=str(raw_id),
                    name=str(name) if name else None,
                    active=bool(workflow.get("active", False)),
                    kind="action" if parsed.kind in ("form", "button") else "display",
                    tagged=has_tag(workflow, DISCOVERY_TAG),
                )
            )
        return results

    # --- discovery -----------------------------------------------------------

    @router.get("/automations/instances", response_model=list[InstanceInfo])
    async def list_instances() -> list[InstanceInfo]:
        """Every registered instance, each with its own live reachability.

        Probed concurrently (``asyncio.gather``), not in series — N
        instances must not mean N sequential timeouts. A down n8n degrades
        that one row to ``connected: false`` (see ``_reachability``, which
        never raises); it never fails, empties, or slows this whole call.
        """
        instances = store.load_instances(settings.automations_file)
        if not instances:
            return []
        results = await asyncio.gather(*(_reachability(entry) for entry in instances))
        return [
            _instance_info(entry, connected=connected)
            for entry, (connected, _detail) in zip(instances, results, strict=True)
        ]

    @router.get("/automations", response_model=AutomationsResponse)
    async def list_automations() -> AutomationsResponse:
        instances = store.load_instances(settings.automations_file)
        if not instances:
            return AutomationsResponse(
                instance=None,
                instances=[],
                workflows=[],
                connected=False,
                detail="no n8n instance registered",
            )

        infos = [_instance_info(entry) for entry in instances]
        # F9 removes this: the old singular `instance` field, byte-identical
        # to before multi-instance support for 0 or 1 registered instances,
        # degrading to None for 2+ — which the pre-migration frontend already
        # renders as its "no instance registered" empty state.
        single = infos[0] if len(infos) == 1 else None

        conn = db()
        try:
            cards = [_card_from_row(conn, row) for row in store.list_workflows(conn)]
        finally:
            conn.close()

        if single is not None:
            # A live reachability check, but never a blocking one: the cards
            # above already came from cache regardless of what happens here.
            connected, detail = await _reachability(instances[0])
        else:
            connected, detail = False, "multiple n8n instances registered"

        return AutomationsResponse(
            instance=single, instances=infos, workflows=cards, connected=connected, detail=detail
        )

    async def _refresh_instance(instance: dict[str, Any]) -> RefreshResult:
        """One instance's refresh pass: pull ``?tags=argus`` workflows from
        n8n, upsert the cache, and drop anything cached but no longer seen.

        Scoped to this instance's real id end-to-end (the write here and the
        read in ``_run_workflow_core``/``_card_from_row`` alike) — refreshing
        instance A can never write into or prune instance B's cached rows.
        """
        api_key = _resolve_key(instance)
        client = factory(instance, api_key)
        instance_id = instance["id"]
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
                    instance_id=instance_id,
                    now=now,
                )
            # Untagging a workflow in n8n (or deleting it) means it drops out
            # of this response — anything cached but not seen this pass loses
            # its card. Scoped to this instance: another instance's cache is
            # never touched by this pass.
            dropped = store.delete_missing_workflows(conn, seen_ids, instance_id=instance_id)
        finally:
            conn.close()

        return RefreshResult(ok=True, count=len(seen_ids), dropped=dropped)

    @router.post("/automations/instances/{instance_id}/refresh", response_model=RefreshResult)
    async def refresh_instance_by_id(instance_id: str) -> RefreshResult:
        instances = store.load_instances(settings.automations_file)
        target = next((entry for entry in instances if entry.get("id") == instance_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"no n8n instance {instance_id}")
        return await _refresh_instance(target)

    @router.post("/automations/refresh", response_model=RefreshResult)
    async def refresh() -> RefreshResult:
        """Compat shim: refreshes every registered instance concurrently and
        aggregates the totals — byte-identical to the old single-instance
        behaviour when exactly one instance is registered. F9 removes this
        in favor of POST /automations/instances/{id}/refresh."""
        instances = store.load_instances(settings.automations_file)
        if not instances:
            raise HTTPException(status_code=409, detail="no n8n instance registered")

        results = await asyncio.gather(*(_refresh_instance(entry) for entry in instances))
        return RefreshResult(
            ok=all(result.ok for result in results),
            count=sum(result.count for result in results),
            dropped=sum(result.dropped for result in results),
        )

    # --- activity events (B5) --------------------------------------------------
    #
    # The ambient log behind the dashboard's status line. Every write here goes
    # through _safe_record_event, which can never raise: a logging write must
    # never be able to fail (or roll back) the run/push/install it describes.

    def _safe_record_event(
        conn: sqlite3.Connection,
        *,
        tag: str,
        text: str,
        instance_id: str,
        subject: str | None = None,
    ) -> None:
        try:
            store.record_event(
                conn, tag=tag, text=text, instance_id=instance_id, subject=subject, now=now
            )
        except Exception:
            logger.exception(
                "failed to record %s activity event for instance %r", tag, instance_id
            )

    def _run_duration_text(run: dict[str, Any]) -> str:
        """A human-scale duration off a finished run's own timestamps."""
        try:
            started = datetime.fromisoformat(run["started_at"])
            finished = datetime.fromisoformat(run["finished_at"])
            return f"{(finished - started).total_seconds():.1f}s"
        except (TypeError, ValueError):
            return "?"

    def _log_run_outcome(conn: sqlite3.Connection, run_id: str) -> None:
        """A RUN (``status == "ok"``) or FAIL (anything else) activity-log
        entry for a run ``finish_run`` just closed out, read back from the
        row it wrote.

        Centralised here — called once from ``_finish_failed`` and once from
        each direct ``store.finish_run`` call in ``_dispatch_result`` — so
        every way a run can end (ack, status, widget, an n8n error, a
        timeout, or a user cancellation) gets exactly one consistent log
        line, keyed off the row's own final status rather than duplicated
        per call site.
        """
        try:
            run = store.get_run(conn, run_id)
            if run is None:
                return
            subject = run["workflow_name"] or run["workflow_id"]
            if run["status"] == "ok":
                exec_part = f", execution {run['execution_id']}" if run["execution_id"] else ""
                text = f"completed in {_run_duration_text(run)}{exec_part}"
                _safe_record_event(
                    conn, tag="RUN", text=text, instance_id=run["instance_id"], subject=subject
                )
            else:
                text = run["message"] or f"run {run['status']}"
                _safe_record_event(
                    conn, tag="FAIL", text=text, instance_id=run["instance_id"], subject=subject
                )
        except Exception:
            logger.exception("failed to log an activity event for run %s", run_id)

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
        _log_run_outcome(conn, run_id)

    def _dispatch_result(
        conn: sqlite3.Connection,
        run_id: str,
        client: N8nClient,
        result: Any,
        *,
        instance_id: str,
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
                instance_id=instance_id,
                now=now,
            )
            store.finish_run(
                conn, run_id, "ok", mode="widget", payload=validated.payload,
                execution_id=execution_id, now=now,
            )
            _log_run_outcome(conn, run_id)
            return RunResponse(
                run_id=run_id, status="ok", mode="widget", payload=validated.payload,
                execution_id=execution_id, execution_url=link,
                widget_slug=slug, widget_kind=validated.kind, instance_id=instance_id,
            )

        if isinstance(body, dict) and "ok" in body:
            ok = bool(body.get("ok"))
            message = body.get("message")
            status = "ok" if ok else "failed"
            store.finish_run(
                conn, run_id, status, mode="status", message=message,
                execution_id=execution_id, now=now,
            )
            _log_run_outcome(conn, run_id)
            return RunResponse(
                run_id=run_id, status=status, mode="status", message=message,
                execution_id=execution_id, execution_url=link,
            )

        # Empty body, or a 2xx JSON shape this router doesn't recognise —
        # both degrade to a bare acknowledgement rather than an error: the
        # trigger plainly succeeded (2xx), it just didn't say anything Argus
        # knows how to interpret further.
        store.finish_run(conn, run_id, "ok", mode="ack", execution_id=execution_id, now=now)
        _log_run_outcome(conn, run_id)
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

    async def _run_workflow_core(
        instance: dict[str, Any], workflow_id: str, request: RunRequest
    ) -> RunResponse:
        """Fire ``workflow_id`` on ``instance`` and record/return the outcome.

        Shared by the instance-scoped route and the unscoped compat shim
        below, once each has resolved which instance actually owns
        ``workflow_id``.
        """
        api_key = _resolve_key(instance)
        instance_id = instance["id"]
        # Not just workflow_id: the in-flight guard is scoped to
        # (instance, workflow) too, so instance A and instance B firing a
        # same-numbered workflow at the same moment cannot false-positive a
        # 409 against each other.
        inflight_key = f"{instance_id}:{workflow_id}"

        conn = db()
        try:
            row = store.get_workflow(conn, workflow_id, instance_id=instance_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no known workflow {workflow_id}")

            definition = row["schema_json"] or {}
            parsed = parse_workflow(definition)
            url = _trigger_url(instance["base_url"], parsed)

            if inflight_key in inflight:
                raise HTTPException(
                    status_code=409,
                    detail=f"{workflow_id} already has a run in flight",
                )
            inflight.add(inflight_key)

            run_id = str(uuid.uuid4())
            store.record_run_started(
                conn, run_id, workflow_id, workflow_name=row["name"],
                instance_id=instance_id, now=now,
            )

            client = factory(instance, api_key)
            transport = client.transport

            if is_async_workflow(definition):
                task = asyncio.create_task(_fire_async(run_id, url, request.payload, transport))
                background_tasks.add(task)

                def _on_done(t: asyncio.Task, _key: str = inflight_key) -> None:
                    background_tasks.discard(t)
                    inflight.discard(_key)

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
                inflight.discard(inflight_key)

            return _dispatch_result(conn, run_id, client, result, instance_id=instance_id)
        finally:
            conn.close()

    @router.post(
        "/automations/instances/{instance_id}/workflows/{workflow_id}/run",
        response_model=RunResponse,
    )
    async def run_workflow_scoped(
        instance_id: str, workflow_id: str, request: RunRequest
    ) -> RunResponse:
        instance = _resolve_instance(instance_id)
        return await _run_workflow_core(instance, workflow_id, request)

    @router.post("/automations/{workflow_id}/run", response_model=RunResponse)
    async def run_workflow(workflow_id: str, request: RunRequest) -> RunResponse:
        """Compat shim. F9 removes this in favor of the instance-scoped route
        above.

        Resolves ``workflow_id`` across every registered instance rather than
        assuming "the one instance": B1's ``(instance_id, id)`` composite key
        made a genuine cross-instance id collision *representable* instead of
        silently wrong (the old single-column key could not even tell two
        same-numbered workflows on different instances apart). A real
        collision here means two different instances each cache a workflow
        under this exact id — this 409s and points at the scoped route rather
        than guessing which one the caller meant.
        """
        instances = store.load_instances(settings.automations_file)
        if not instances:
            raise HTTPException(status_code=409, detail="no n8n instance registered")

        conn = db()
        try:
            matches = [
                entry
                for entry in instances
                if store.get_workflow(conn, workflow_id, instance_id=entry["id"]) is not None
            ]
        finally:
            conn.close()

        if not matches:
            raise HTTPException(status_code=404, detail=f"no known workflow {workflow_id}")
        if len(matches) > 1:
            names = ", ".join(f"{m['name']} ({m['id']})" for m in matches)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"workflow id {workflow_id!r} exists on multiple instances ({names}) — "
                    f"use POST /automations/instances/{{instance_id}}/workflows/"
                    f"{workflow_id}/run"
                ),
            )
        return await _run_workflow_core(matches[0], workflow_id, request)

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

    # --- cancel (B7) -------------------------------------------------------

    @router.post("/automations/runs/{run_id}/cancel", response_model=RunOut)
    async def cancel_run(run_id: str) -> RunOut:
        """Best-effort cancellation of a still-``running`` run.

        Marked ``status='failed'`` on success — never a ``'cancelled'``
        value. SQLite cannot ``ALTER`` a ``CHECK`` constraint, so adding a
        third status label to ``automation_runs.status`` would force the
        same kind of full-table rebuild ``_migrate_workflow_key`` did for
        ``automation_workflows``'s key, just to add a label; ``'failed'``
        plus a message that plainly says "cancelled by user" carries the
        same information without that cost.
        """
        conn = db()
        try:
            run = store.get_run(conn, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"no run {run_id}")
            if run["status"] != "running":
                raise HTTPException(
                    status_code=409,
                    detail=f"run {run_id} is already {run['status']} — cannot cancel it",
                )

            execution_id = run["execution_id"]
            instance_id = run["instance_id"]
            inflight_key = f"{instance_id}:{run['workflow_id']}"

            if execution_id:
                instance = _resolve_instance(instance_id)
                api_key = _resolve_key(instance)
                client = factory(instance, api_key)
                try:
                    await client.stop_execution(execution_id)
                except N8nError as exc:
                    raise HTTPException(
                        status_code=502, detail=f"could not stop execution in n8n: {exc}"
                    ) from exc
                message = "cancelled by user"
            else:
                # No execution id was ever recorded for this run, so there is
                # nothing to hand n8n's stop endpoint — Argus gives up on the
                # run locally, but must not silently claim the underlying
                # trigger was actually stopped in n8n; it may still be
                # running there.
                message = (
                    "cancelled by user — no execution id was recorded for this run, so "
                    "n8n was never asked to stop it and may still be running it"
                )

            _finish_failed(
                conn, run_id, status="failed", mode=None, message=message,
                execution_id=execution_id,
            )

            # A cancelled run must not leave its own workflow permanently
            # unrunnable — release the in-flight lock immediately rather than
            # waiting for whatever originally set it to notice.
            inflight.discard(inflight_key)

            updated = store.get_run(conn, run_id)
            assert updated is not None  # just written above
            return RunOut(**updated)
        finally:
            conn.close()

    @router.get("/automations/sources", response_model=SourceProvenance)
    def source_provenance() -> SourceProvenance:
        """Which path is currently answering for calendar and tasks.

        Read-only provenance for the dashboard's `VIA N8N` markers. Answered
        by ``sources.answered_by``, i.e. by the same freshness rule the data
        path itself uses — deriving it in the frontend from widget state
        would be a second copy of that decision, free to drift from the one
        that actually picks the data.
        """
        conn = db()
        try:
            return SourceProvenance(
                calendar=sources.answered_by(conn, sources.CALENDAR_SLUG, now=now),
                tasks=sources.answered_by(conn, sources.TASKS_SLUG, now=now),
            )
        finally:
            conn.close()

    # --- activity events (B5) ---------------------------------------------

    @router.get("/automations/events", response_model=list[EventOut])
    def list_events_route(
        tag: str | None = None, instance_id: str | None = None, limit: int = 100
    ) -> list[EventOut]:
        """Newest-first activity log, optionally filtered by tag and/or
        instance.

        ``tag`` is normalised to uppercase here before it ever reaches
        ``store.list_events`` — the UI sends lowercase filter names
        (``run``/``push``/``fail``/...) but ``automation_events.tag`` (and
        ``store.EVENT_TAGS``) are uppercase, matching the CHECK constraint.
        """
        normalized_tag = tag.upper() if tag is not None else None
        if normalized_tag is not None and normalized_tag not in store.EVENT_TAGS:
            raise HTTPException(
                status_code=422, detail=f"tag must be one of {', '.join(store.EVENT_TAGS)}"
            )
        conn = db()
        try:
            rows = store.list_events(
                conn, tag=normalized_tag, instance_id=instance_id, limit=limit
            )
            return [EventOut(**row) for row in rows]
        finally:
            conn.close()

    # --- widgets ---------------------------------------------------------------

    def _resolve_widget_instance(
        conn: sqlite3.Connection, slug: str, instance_id: str | None
    ) -> str:
        """The real ``instance_id`` a bare widget ``slug`` refers to.

        An explicit ``instance_id`` (query param) is trusted as-is — a wrong
        one simply 404s downstream, same as an unknown slug always has.
        Omitted, the slug is resolved across every instance that currently
        has a widget at it: B1's ``(instance_id, slug)`` key means a bare
        slug that used to be unambiguous no longer is, so this 409s rather
        than silently picking one when more than one instance answers to it.
        """
        if instance_id is not None:
            return instance_id
        matches = [
            w["instance_id"]
            for w in store.list_widgets(conn, include_hidden=True)
            if w["slug"] == slug
        ]
        if not matches:
            raise HTTPException(status_code=404, detail=f"no widget {slug}")
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"widget {slug!r} exists on multiple instances — pass "
                    "?instance_id= to disambiguate"
                ),
            )
        return matches[0]

    @router.get("/automations/widgets", response_model=list[WidgetOut])
    def list_widgets_route(
        include_hidden: bool = False, instance_id: str | None = None
    ) -> list[WidgetOut]:
        """Every instance's widgets by default — the dashboard shows
        everything at once. Pass ``instance_id`` to scope to one instance."""
        conn = db()
        try:
            rows = store.list_widgets(
                conn, include_hidden=include_hidden, instance_id=instance_id
            )
            return [WidgetOut(**row, state=store.widget_state(row, now=now)) for row in rows]
        finally:
            conn.close()

    @router.post("/automations/widgets/layout/reset", response_model=RefreshResult)
    def reset_widget_layout() -> RefreshResult:
        """Hand every widget back to auto-placement.

        Registered *before* the `{slug}` routes below on purpose: FastAPI
        matches in declaration order, and `layout` would otherwise be read as
        a slug. Exists because every layout write implicitly locks the widget
        it touches — correct, since nobody drags a card by accident, but a
        mode with no way out is a trap.
        """
        conn = db()
        try:
            released = store.reset_layout(conn)
            return RefreshResult(ok=True, count=released, dropped=0)
        finally:
            conn.close()

    @router.patch("/automations/widgets/{slug}", response_model=WidgetOut)
    def patch_widget(
        slug: str, request: WidgetPatchRequest, instance_id: str | None = None
    ) -> WidgetOut:
        conn = db()
        try:
            resolved_id = _resolve_widget_instance(conn, slug, instance_id)
            existing = store.get_widget(conn, slug, instance_id=resolved_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no widget {slug}")

            touched = any(
                field is not None
                for field in (
                    request.pinned,
                    request.hidden,
                    request.position,
                    request.grid_cols,
                    request.grid_rows,
                )
            )
            try:
                store.set_widget_flags(
                    conn,
                    slug,
                    pinned=request.pinned,
                    hidden=request.hidden,
                    position=request.position,
                    grid_cols=request.grid_cols,
                    grid_rows=request.grid_rows,
                    # Any manual pin/hide/reorder/resize is the user taking
                    # control of THIS widget — an auto-layout pass must not
                    # silently reshuffle a card they just placed by hand.
                    # Deliberately per-widget rather than the single global
                    # `layout_taken_control` pref this replaces: freezing the
                    # whole dashboard the first time someone nudges one card
                    # would mean every later automation arrives needing manual
                    # placement, and auto-place is the thing that makes
                    # installing one cost zero configuration.
                    layout_locked=True if touched else None,
                    instance_id=resolved_id,
                )
            except ValueError as exc:
                # Range violation on grid_cols/grid_rows. 422, not 500 — the
                # request is malformed, not the server.
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            row = store.get_widget(conn, slug, instance_id=resolved_id)
            assert row is not None  # just confirmed above
            return WidgetOut(**row, state=store.widget_state(row, now=now))
        finally:
            conn.close()

    @router.delete("/automations/widgets/{slug}", response_model=ConnectResult)
    def delete_widget_route(slug: str, instance_id: str | None = None) -> ConnectResult:
        conn = db()
        try:
            resolved_id = _resolve_widget_instance(conn, slug, instance_id)
            existing = store.get_widget(conn, slug, instance_id=resolved_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"no widget {slug}")
            store.delete_widget(conn, slug, instance_id=resolved_id)
        finally:
            conn.close()
        return ConnectResult(ok=True, detail=f"{slug} removed")

    # --- template gallery --------------------------------------------------
    #
    # Two routes only, appended for the catalog.py chunk (the shipped
    # workflow template gallery): list the bundled templates, and install one
    # into the registered n8n instance. See catalog.py's module docstring for
    # why every bundled template's trigger sets an explicit respond mode, and
    # InstallResult.open_in_n8n's docstring above for why credential granting
    # is the one step this can't finish on the user's behalf.

    @router.get("/automations/templates", response_model=list[TemplateOut])
    def list_templates_route() -> list[TemplateOut]:
        conn = db()
        try:
            # "Installed" is judged against the cached workflow list (the
            # same cache the dashboard cards render from), not a live n8n
            # call — this endpoint must render instantly and offline exactly
            # like GET /automations does, not block on network reachability.
            cached_names = {row["name"] for row in store.list_workflows(conn) if row["name"]}
        finally:
            conn.close()
        return [
            TemplateOut.from_template(template, installed=template.name in cached_names)
            for template in catalog.list_templates()
        ]

    async def _install_template_core(
        template_id: str, resolve_instance: Callable[[], dict[str, Any]]
    ) -> InstallResult:
        """Shared install body. ``resolve_instance`` is a thunk, not an
        already-resolved instance, so the unknown-template/missing-callback-
        URL checks below run (and can 404/409 on their own terms) before
        instance resolution is even attempted — preserving the original
        single-instance route's check ordering for the scoped route and the
        compat shim alike."""
        try:
            catalog.load_definition(template_id)
        except catalog.UnknownTemplate as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # The callback URL comes from the user-supplied public tunnel, not
        # from this n8n instance's own base_url — installing a template that
        # posts back to an empty string would fail the first time it fires,
        # invisibly (n8n owns the workflow at that point; nothing surfaces
        # the failure back to Argus). Refusing now, loudly, beats that.
        if not settings.external_base_url:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the public callback URL is not configured yet — set it before "
                    "installing a template that posts back to Argus"
                ),
            )

        instance = resolve_instance()
        api_key = _resolve_key(instance)

        # B4: the token baked into the workflow must be *this* instance's own
        # token, not any registered instance's. A workflow's callbacks all
        # authenticate with whatever token gets baked in here — installing
        # with another instance's token would make every callback from this
        # workflow authenticate as the wrong instance, mis-attributing every
        # push it ever makes.
        instance_id = instance["id"]
        token = external_auth.get_token(instance_id)
        if token is None:
            token = external_auth.generate_token(instance_id)

        definition = catalog.render_definition(
            template_id, callback_url=settings.external_base_url, token=token
        )

        client = factory(instance, api_key)
        try:
            created = await client.create_workflow(definition)
            workflow_id = str(created.get("id"))
            await client.activate_workflow(workflow_id)
        except N8nError as exc:
            raise HTTPException(
                status_code=502, detail=f"could not install template in n8n: {exc}"
            ) from exc

        install_subject = definition.get("name") if isinstance(definition, dict) else None
        event_conn = db()
        try:
            _safe_record_event(
                event_conn,
                tag="INSTALL",
                text=f"installed as workflow {workflow_id}",
                instance_id=instance_id,
                subject=install_subject or template_id,
            )
        finally:
            event_conn.close()

        # Reuse discovery's own cache-refresh pass rather than duplicating its
        # upsert/drop logic here: the newly-created workflow only becomes a
        # dashboard card once it has been through the same pass every other
        # refresh runs. Scoped to the instance just installed into, not the
        # POST /automations/refresh compat shim's refresh-everything — with
        # 2+ instances registered, installing into one must not also pay for
        # (or wait on) refreshing every other one.
        await _refresh_instance(instance)

        open_in_n8n = f"{instance['base_url'].rstrip('/')}/workflow/{workflow_id}"
        return InstallResult(workflow_id=workflow_id, open_in_n8n=open_in_n8n)

    @router.post(
        "/automations/instances/{instance_id}/templates/{template_id}/install",
        response_model=InstallResult,
        status_code=201,
    )
    async def install_template_scoped(instance_id: str, template_id: str) -> InstallResult:
        return await _install_template_core(template_id, lambda: _resolve_instance(instance_id))

    @router.post(
        "/automations/templates/{template_id}/install",
        response_model=InstallResult,
        status_code=201,
    )
    async def install_template(template_id: str) -> InstallResult:
        """Compat shim, valid only while exactly one instance is registered —
        409 otherwise, pointing at the scoped route above. F9 removes this."""
        return await _install_template_core(template_id, _only_instance)

    # --- the inbound surface's own credential -------------------------------
    #
    # These live on the LOCAL /api (localhost, unauthenticated like the rest of
    # it) and describe the *external* surface. They are not mounted on the
    # external app itself — a public endpoint that hands out the credential
    # guarding it would defeat the point.

    def _external_surface_info(instance_id: str) -> ExternalSurfaceInfo:
        return ExternalSurfaceInfo(
            enabled=settings.external_enabled,
            port=settings.external_port,
            base_url=settings.external_base_url,
            token_state=external_auth.token_state(instance_id),
        )

    @router.get(
        "/automations/instances/{instance_id}/external", response_model=ExternalSurfaceInfo
    )
    def external_surface_scoped(instance_id: str) -> ExternalSurfaceInfo:
        """What the user needs in order to point a tunnel at Argus, for one
        specific instance's token.

        Deliberately does NOT include the token value. Reading this is a page
        load; handing out a live credential on every dashboard render is not
        something to do casually, so the value is only ever returned by the
        explicit issue/rotate action below.
        """
        _resolve_instance(instance_id)  # 404 on an unknown instance
        return _external_surface_info(instance_id)

    # F9 removes this
    @router.get("/automations/external", response_model=ExternalSurfaceInfo)
    def external_surface() -> ExternalSurfaceInfo:
        """Compat shim: identical behaviour to before multi-instance support
        for exactly one registered instance, 409 otherwise — see
        ``_only_instance``. F9 removes this in favor of the scoped route
        above."""
        instance = _only_instance()
        return _external_surface_info(instance["id"])

    def _issue_external_token(instance_id: str) -> ExternalTokenResult:
        rotated = external_auth.token_state(instance_id) == KEY_PRESENT
        token = external_auth.rotate_token(instance_id)
        return ExternalTokenResult(
            token=token,
            rotated=rotated,
            header_name="Authorization",
            header_value=f"Bearer {token}",
            base_url=settings.external_base_url,
        )

    @router.post(
        "/automations/instances/{instance_id}/external/token",
        response_model=ExternalTokenResult,
    )
    def issue_external_token_scoped(instance_id: str) -> ExternalTokenResult:
        """Issue or rotate one instance's bearer token, returning it **once**
        for copying.

        Rotating invalidates that instance's old token immediately, which is
        the point: the recovery for a leaked token is to press this and
        re-paste the credential into that instance's n8n workflows. The value
        is returned here and never again — the keyring holds the only copy,
        and there is no read-it-back endpoint. Only ``instance_id``'s token is
        touched; every other instance keeps authenticating with its own.
        """
        _resolve_instance(instance_id)  # 404 on an unknown instance
        return _issue_external_token(instance_id)

    # F9 removes this
    @router.post("/automations/external/token", response_model=ExternalTokenResult)
    def issue_external_token() -> ExternalTokenResult:
        """Compat shim: identical behaviour to before multi-instance support
        for exactly one registered instance, 409 otherwise — see
        ``_only_instance``. F9 removes this in favor of the scoped route
        above."""
        instance = _only_instance()
        return _issue_external_token(instance["id"])

    return router


__all__ = ["build_automations_router"]
