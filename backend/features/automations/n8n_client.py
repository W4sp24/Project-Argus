"""HTTP client for n8n's public REST API.

This is the transport layer only: authentication, URL construction, error
classification, and the two things that fire a workflow (a form/webhook POST
and a connection probe). Nothing here parses a workflow definition, sanitizes
a payload, persists anything, or interprets what a run's response body means
— those are other chunks' jobs (``schema.py``, ``sanitize.py``, ``store.py``,
``router.py``).

Modeled directly on :mod:`backend.agent.openai_compat` (the dataclass +
``transport`` seam) and :mod:`backend.features.integrations.mcp_client` (the
never-raises probe contract).

**Tags-filter fallback.** ``list_workflows(tags=...)`` passes ``tags`` as a
query parameter, but it is unverified whether every n8n version actually
applies that filter server-side — some releases have been known to ignore
unrecognized or lightly-supported query params rather than reject them. If
the server sends back a workflow that does not carry the requested tag,
returning it would mean Argus manages (and potentially deletes) a workflow
the user never asked it to touch. So the filter is re-applied client-side on
every response as a deliberate belt-and-braces fallback, whether or not the
server-side filter worked.

**Credential-creation asymmetry.** :meth:`N8nClient.create_credential` is the
single call most likely to fail against an otherwise well-supported n8n,
because the Header Auth credential-type schema has moved between n8n
releases. A 400/422 there means "n8n didn't like this schema", not "the
integration is broken" or "the key is bad" — so it returns ``None`` rather
than raising, and the caller's fallback is to ask the user to paste the
credential into n8n by hand. A 401/403 (bad key) or a connection failure are
different problems and still raise.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Matches backend.agent.adapters.DEFAULT_TIMEOUT_SECONDS — same rationale:
#: workflow creation/activation calls can be slow, and there is no separate
#: budget carved out for n8n specifically.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: A probe that hasn't heard back by now is not going to — mirrors
#: mcp_client.PROBE_TIMEOUT_SECONDS.
PROBE_TIMEOUT_SECONDS = 20.0

#: n8n's public API authenticates with this header, not ``Authorization``.
API_KEY_HEADER = "X-N8N-API-KEY"


# --- errors -------------------------------------------------------------


class N8nError(RuntimeError):
    """Base class for every error this module raises."""


class N8nAuthError(N8nError):
    """401/403 — the API key was rejected.

    Kept distinct from :class:`N8nUnavailable` because the design marks the
    instance ``failing`` without discarding the stored key on this error
    specifically: a wrong/rotated key is a different, more actionable
    problem than "n8n is down".
    """


class N8nUnavailable(N8nError):  # noqa: N818 - name mandated by the chunk brief
    """n8n could not be reached at all: connection errors, timeouts, 5xx."""


class N8nNotFound(N8nError):  # noqa: N818 - name mandated by the chunk brief
    """404 — the workflow/execution/credential id doesn't exist."""


class N8nTimeout(N8nError):  # noqa: N818 - name mandated by the chunk brief
    """A :func:`fire` call timed out.

    Deliberately not just an :class:`N8nUnavailable`: firing a workflow is
    fire-and-forget from n8n's perspective, so a client-side timeout does not
    mean the run failed — it may still be executing. The caller needs to
    render "TIMED OUT" with a link to the (possibly still-running) execution,
    which requires catching this specifically rather than a generic error.
    """


# --- results --------------------------------------------------------------


@dataclass(frozen=True)
class N8nRunResult:
    """What came back from firing a workflow's trigger URL."""

    status_code: int
    body: Any
    elapsed_ms: int
    execution_id: str | None


@dataclass(frozen=True)
class N8nProbeResult:
    """What the "Test connection" dialog renders."""

    ok: bool
    detail: str
    latency_ms: int | None
    workflow_count: int | None


# --- url helpers ------------------------------------------------------------


def _normalize_base_url(base_url: str) -> str:
    """Strip trailing slashes so URL joins never produce a doubled ``//``."""
    return base_url.strip().rstrip("/")


def _api_url(base_url: str, path: str) -> str:
    """``{base}/api/v1{path}`` — ``path`` must include its own leading ``/``."""
    return f"{_normalize_base_url(base_url)}/api/v1{path}"


def form_url(base_url: str, webhook_id: str) -> str:
    """The public URL for an n8n Form Trigger."""
    return f"{_normalize_base_url(base_url)}/form/{webhook_id}"


def webhook_url(base_url: str, path: str) -> str:
    """The public URL for an n8n Webhook Trigger."""
    return f"{_normalize_base_url(base_url)}/webhook/{path}"


# --- error classification ---------------------------------------------------


def _error_detail(response: httpx.Response) -> str:
    """A readable one-liner for an error response, JSON or not."""
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:200] if text else f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str) and message:
            return message
    return response.text.strip()[:200] or f"HTTP {response.status_code}"


def _raise_for_status(response: httpx.Response) -> None:
    """Turn a non-2xx status into the right :class:`N8nError` subclass.

    Callers that need to treat a specific status as non-fatal (400/422 in
    ``create_credential``, 404/405 in ``stop_execution``) check
    ``response.status_code`` *before* calling this, so it is only reached for
    statuses that really are unrecoverable for that call.
    """
    if response.status_code < 400:
        return
    detail = _error_detail(response)
    if response.status_code in (401, 403):
        raise N8nAuthError(detail)
    if response.status_code == 404:
        raise N8nNotFound(detail)
    if response.status_code >= 500:
        raise N8nUnavailable(f"{response.status_code}: {detail}")
    raise N8nError(f"{response.status_code}: {detail}")


def _parse_body(response: httpx.Response) -> Any:
    """The JSON body when the response is JSON, else the raw text."""
    try:
        return response.json()
    except ValueError:
        return response.text


def _extract_execution_id(body: Any) -> str | None:
    """Pull an execution id out of a (possibly error) response body.

    n8n puts the execution id in the error body on a failed (500) run, under
    ``executionId`` at the top level or nested under ``data`` — this checks
    both spellings in both places rather than assuming one n8n version's
    shape.
    """
    if not isinstance(body, dict):
        return None
    for key in ("executionId", "execution_id"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("executionId", "execution_id", "id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _has_tag(workflow: dict[str, Any], tag: str) -> bool:
    """Whether ``workflow["tags"]`` carries ``tag``, in either shape n8n uses.

    Tags come back either as plain strings or as ``{"id": ..., "name": ...}``
    objects depending on n8n version/endpoint.
    """
    tags = workflow.get("tags")
    if not isinstance(tags, list):
        return False
    for entry in tags:
        if isinstance(entry, str) and entry == tag:
            return True
        if isinstance(entry, dict) and entry.get("name") == tag:
            return True
    return False


def _unwrap_list(payload: Any) -> list[Any]:
    """n8n usually wraps list responses as ``{"data": [...]}``; tolerate a bare list too."""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else []
    if isinstance(payload, list):
        return payload
    return []


# --- client -----------------------------------------------------------------


@dataclass
class N8nClient:
    """A thin async wrapper over one n8n instance's public REST API."""

    base_url: str
    api_key: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = None

    def _headers(self) -> dict[str, str]:
        return {API_KEY_HEADER: self.api_key, "Accept": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, headers=self._headers()
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue one authenticated request under ``{base}/api/v1``.

        Connection failures, timeouts, and any other transport-level error
        are folded into :class:`N8nUnavailable` here. HTTP status codes are
        left to the caller (via :func:`_raise_for_status`), since a few call
        sites need to treat specific statuses as non-fatal.
        """
        try:
            async with self._client() as client:
                return await client.request(
                    method, _api_url(self.base_url, path), params=params, json=json_body
                )
        except httpx.HTTPError as exc:
            raise N8nUnavailable(f"could not reach n8n at {self.base_url}: {exc}") from exc

    # --- workflows --------------------------------------------------------

    async def list_workflows(self, *, tags: str | None = None) -> list[dict]:
        """``GET /workflows``, unwrapping ``{"data": [...]}``.

        See the module docstring: when ``tags`` is given, the result is also
        filtered client-side, dropping anything the server returned that
        doesn't actually carry that tag.
        """
        params = {"tags": tags} if tags else None
        response = await self._request("GET", "/workflows", params=params)
        _raise_for_status(response)
        workflows = [w for w in _unwrap_list(_parse_body(response)) if isinstance(w, dict)]
        if tags:
            workflows = [w for w in workflows if _has_tag(w, tags)]
        return workflows

    async def get_workflow(self, workflow_id: str) -> dict:
        """``GET /workflows/{id}``."""
        response = await self._request("GET", f"/workflows/{workflow_id}")
        _raise_for_status(response)
        return _parse_body(response)

    async def create_workflow(self, definition: dict) -> dict:
        """``POST /workflows``."""
        response = await self._request("POST", "/workflows", json_body=definition)
        _raise_for_status(response)
        return _parse_body(response)

    async def activate_workflow(self, workflow_id: str) -> dict:
        """``POST /workflows/{id}/activate``."""
        response = await self._request("POST", f"/workflows/{workflow_id}/activate")
        _raise_for_status(response)
        return _parse_body(response)

    async def delete_workflow(self, workflow_id: str) -> None:
        """``DELETE /workflows/{id}``."""
        response = await self._request("DELETE", f"/workflows/{workflow_id}")
        _raise_for_status(response)

    # --- executions ---------------------------------------------------------

    async def get_execution(self, execution_id: str) -> dict:
        """``GET /executions/{id}``."""
        response = await self._request("GET", f"/executions/{execution_id}")
        _raise_for_status(response)
        return _parse_body(response)

    async def stop_execution(self, execution_id: str) -> None:
        """``POST /executions/{id}/stop``.

        Some n8n builds don't expose this endpoint at all. The UI offers a
        Cancel action unconditionally, so a 404 (route missing) or 405
        (method not allowed there) must degrade to a quiet no-op rather than
        raise — cancelling an execution n8n won't let you cancel is not a
        failure the user needs to see.
        """
        response = await self._request("POST", f"/executions/{execution_id}/stop")
        if response.status_code in (404, 405):
            logger.info(
                "n8n stop_execution: no stop endpoint for execution %s (status %s)",
                execution_id,
                response.status_code,
            )
            return
        _raise_for_status(response)

    def execution_url(self, execution_id: str) -> str:
        """Deep link to the execution in the n8n UI.

        Kept as the single place this URL is built so its shape can be
        corrected later without hunting through callers.
        """
        return f"{_normalize_base_url(self.base_url)}/workflow/executions/{execution_id}"

    # --- credentials ----------------------------------------------------

    async def create_credential(
        self, name: str, credential_type: str, data: dict
    ) -> dict | None:
        """``POST /credentials``.

        Returns ``None`` on a 400/422 (n8n rejected the credential-type
        schema — this drifts between n8n releases) so the caller can fall
        back to asking the user to paste the credential into n8n manually.
        This is a deliberate asymmetry, not a blanket "swallow errors": a
        genuine auth failure (401/403) or an unreachable n8n still raises,
        because those are not "ask the user to do it by hand" situations.
        """
        response = await self._request(
            "POST",
            "/credentials",
            json_body={"name": name, "type": credential_type, "data": data},
        )
        if response.status_code in (400, 422):
            logger.warning(
                "n8n rejected the %r credential schema (status %s) — "
                "falling back to manual credential entry",
                credential_type,
                response.status_code,
            )
            return None
        _raise_for_status(response)
        return _parse_body(response)

    async def get_credential_schema(self, credential_type: str) -> dict | None:
        """``GET /credentials/schema/{type}``.

        ``None`` when the schema endpoint itself isn't available (404) —
        this is advisory metadata some n8n builds omit, not something worth
        crashing over.
        """
        response = await self._request("GET", f"/credentials/schema/{credential_type}")
        if response.status_code == 404:
            return None
        _raise_for_status(response)
        return _parse_body(response)


# --- firing a workflow --------------------------------------------------


async def fire(
    url: str,
    payload: dict,
    *,
    basic_auth: tuple[str, str] | None = None,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> N8nRunResult:
    """POST ``payload`` to a fully-built trigger URL (form or webhook).

    This is *not* an ``/api/v1`` call: it hits the trigger URL directly, so
    it does not send ``X-N8N-API-KEY`` — a form/webhook trigger is a public
    endpoint on the n8n side, gated (if at all) by ``basic_auth`` or by the
    secrecy of the URL itself, not by the management API key.

    This deliberately does not interpret the response body — no ack/status
    detection, no widget dispatch. It reports what happened faithfully;
    turning that into UI state is another module's job.

    ``transport`` is not part of the brief's literal signature but is added
    here for the same reason every other module-level helper in this
    codebase takes one (see ``list_models`` in ``openai_compat.py``): without
    it this function is untestable without real network I/O.
    """
    started = time.monotonic()
    auth = httpx.BasicAuth(*basic_auth) if basic_auth else None
    try:
        async with httpx.AsyncClient(
            timeout=timeout or DEFAULT_TIMEOUT_SECONDS, transport=transport
        ) as client:
            response = await client.post(url, json=payload, auth=auth)
    except httpx.TimeoutException as exc:
        raise N8nTimeout(f"timed out firing {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise N8nUnavailable(f"could not reach {url}: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    body = _parse_body(response)
    return N8nRunResult(
        status_code=response.status_code,
        body=body,
        elapsed_ms=elapsed_ms,
        execution_id=_extract_execution_id(body),
    )


# --- probe --------------------------------------------------------------


async def _do_probe(
    base_url: str, api_key: str, timeout: float, transport: httpx.AsyncBaseTransport | None
) -> N8nProbeResult:
    started = time.monotonic()
    headers = {API_KEY_HEADER: api_key, "Accept": "application/json"}
    url = _api_url(base_url, "/workflows")

    async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers) as client:
        try:
            response = await client.get(url, params={"limit": 1})
        except httpx.ConnectError as exc:
            return N8nProbeResult(
                ok=False,
                detail=f"connection refused — check the URL and that n8n is running ({exc})",
                latency_ms=None,
                workflow_count=None,
            )
        except httpx.TimeoutException as exc:
            return N8nProbeResult(
                ok=False,
                detail=f"timed out waiting for n8n to respond ({exc})",
                latency_ms=None,
                workflow_count=None,
            )
        except httpx.HTTPError as exc:
            return N8nProbeResult(
                ok=False, detail=f"could not reach n8n: {exc}", latency_ms=None, workflow_count=None
            )

    latency_ms = int((time.monotonic() - started) * 1000)
    content_type = response.headers.get("content-type", "")

    if response.status_code in (401, 403):
        return N8nProbeResult(
            ok=False,
            detail="n8n rejected the API key — check it and try again",
            latency_ms=latency_ms,
            workflow_count=None,
        )

    if response.status_code == 404:
        if "json" not in content_type:
            # A 404 that isn't even JSON means this URL didn't reach n8n's
            # API router at all — most likely the base URL is wrong, or it
            # points at n8n's web UI rather than its API.
            return N8nProbeResult(
                ok=False,
                detail="that URL doesn't look like an n8n instance (got an HTML page, not JSON)",
                latency_ms=latency_ms,
                workflow_count=None,
            )
        # A JSON 404 means something answered as n8n but the route is
        # missing — the most plausible cause is n8n's Public API being
        # switched off (a Community-edition setting), not a wrong URL.
        return N8nProbeResult(
            ok=False,
            detail=(
                "n8n responded but /api/v1/workflows was not found — the Public API "
                "may be disabled on this instance (check n8n's API settings)"
            ),
            latency_ms=latency_ms,
            workflow_count=None,
        )

    if response.status_code >= 400:
        return N8nProbeResult(
            ok=False,
            detail=f"n8n returned {response.status_code}: {_error_detail(response)}",
            latency_ms=latency_ms,
            workflow_count=None,
        )

    if "json" not in content_type:
        return N8nProbeResult(
            ok=False,
            detail="that URL doesn't look like an n8n instance (response wasn't JSON)",
            latency_ms=latency_ms,
            workflow_count=None,
        )

    workflows = _unwrap_list(_parse_body(response))
    return N8nProbeResult(
        ok=True, detail="connected", latency_ms=latency_ms, workflow_count=len(workflows)
    )


async def probe(
    base_url: str,
    api_key: str,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> N8nProbeResult:
    """Hit ``GET /api/v1/workflows?limit=1`` and report the result. Never raises.

    ``detail`` is written for a human reading a "Test connection" dialog:
    it distinguishes a rejected API key, a URL that doesn't look like n8n at
    all, n8n's Public API being switched off, a refused connection, and a
    timeout — because each of those has a different fix, and "connection
    failed" alone sends the user nowhere useful.
    """
    try:
        return await asyncio.wait_for(
            _do_probe(base_url, api_key, timeout, transport), timeout=PROBE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return N8nProbeResult(
            ok=False,
            detail=(
                f"no response within {int(PROBE_TIMEOUT_SECONDS)}s — "
                "n8n did not respond in time"
            ),
            latency_ms=None,
            workflow_count=None,
        )
    except Exception as exc:  # noqa: BLE001 - the user sees the reason, not a 500
        return N8nProbeResult(
            ok=False, detail=f"{type(exc).__name__}: {exc}", latency_ms=None, workflow_count=None
        )
