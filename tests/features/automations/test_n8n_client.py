"""Tests for the n8n public-API HTTP client.

Every case runs against ``httpx.MockTransport`` — no live n8n, no network,
no ``time.sleep`` — matching the pattern in ``tests/agent/test_adapters.py``.
"""

from __future__ import annotations

import httpx
import pytest

from backend.features.automations.n8n_client import (
    API_KEY_HEADER,
    N8nAuthError,
    N8nClient,
    N8nNotFound,
    N8nTimeout,
    N8nUnavailable,
    fire,
    form_url,
    probe,
    webhook_url,
)

API_KEY = "n8n-api-key-123"


def client_for(handle, base_url: str = "http://localhost:5678") -> N8nClient:
    return N8nClient(base_url=base_url, api_key=API_KEY, transport=httpx.MockTransport(handle))


def json_response(status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --- auth header + base URL normalization -----------------------------------


@pytest.mark.anyio
async def test_sends_api_key_header_not_authorization() -> None:
    seen_headers: list[httpx.Headers] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return json_response(200, {"data": []})

    client = client_for(handle)
    await client.list_workflows()

    assert seen_headers[0][API_KEY_HEADER] == API_KEY
    assert "authorization" not in seen_headers[0]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:5678",
        "http://localhost:5678/",
        "http://localhost:5678/n8n",
        "http://localhost:5678/n8n/",
    ],
)
@pytest.mark.anyio
async def test_base_url_normalization(base_url: str) -> None:
    seen_urls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return json_response(200, {"data": []})

    client = client_for(handle, base_url=base_url)
    await client.list_workflows()

    expected_base = base_url.rstrip("/")
    assert seen_urls[0] == f"{expected_base}/api/v1/workflows"


# --- list_workflows -----------------------------------------------------


@pytest.mark.anyio
async def test_list_workflows_unwraps_data_envelope() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]})

    client = client_for(handle)
    workflows = await client.list_workflows()
    assert [w["id"] for w in workflows] == ["1", "2"]


@pytest.mark.anyio
async def test_list_workflows_tolerates_bare_list() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(200, [{"id": "1", "name": "a"}])

    client = client_for(handle)
    workflows = await client.list_workflows()
    assert [w["id"] for w in workflows] == ["1"]


@pytest.mark.anyio
async def test_list_workflows_sends_tags_query_param() -> None:
    seen_params: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_params.append(request.url.params.get("tags"))
        return json_response(200, {"data": []})

    client = client_for(handle)
    await client.list_workflows(tags="argus")
    assert seen_params[0] == "argus"


@pytest.mark.anyio
async def test_list_workflows_filters_client_side_when_server_ignores_tags() -> None:
    """Belt-and-braces fallback: never return a workflow lacking the requested tag."""

    def handle(request: httpx.Request) -> httpx.Response:
        # Server pretends the tags filter isn't supported and returns everything.
        return json_response(
            200,
            {
                "data": [
                    {"id": "1", "name": "tagged", "tags": [{"id": "t1", "name": "argus"}]},
                    {"id": "2", "name": "untagged", "tags": [{"id": "t2", "name": "other"}]},
                    {"id": "3", "name": "string-tagged", "tags": ["argus"]},
                    {"id": "4", "name": "no-tags"},
                ]
            },
        )

    client = client_for(handle)
    workflows = await client.list_workflows(tags="argus")
    ids = {w["id"] for w in workflows}
    assert ids == {"1", "3"}


# --- error classification ---------------------------------------------------


@pytest.mark.anyio
async def test_401_raises_auth_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(401, {"message": "unauthorized"})

    client = client_for(handle)
    with pytest.raises(N8nAuthError):
        await client.list_workflows()


@pytest.mark.anyio
async def test_404_raises_not_found() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(404, {"message": "not found"})

    client = client_for(handle)
    with pytest.raises(N8nNotFound):
        await client.get_workflow("missing")


@pytest.mark.anyio
async def test_500_raises_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(500, {"message": "internal error"})

    client = client_for(handle)
    with pytest.raises(N8nUnavailable):
        await client.list_workflows()


@pytest.mark.anyio
async def test_connect_error_raises_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = client_for(handle)
    with pytest.raises(N8nUnavailable):
        await client.list_workflows()


# --- create_credential --------------------------------------------------


@pytest.mark.anyio
async def test_create_credential_returns_none_on_400() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(400, {"message": "schema rejected"})

    client = client_for(handle)
    result = await client.create_credential("argus-header-auth", "httpHeaderAuth", {"name": "X"})
    assert result is None


@pytest.mark.anyio
async def test_create_credential_returns_none_on_422() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(422, {"message": "unprocessable"})

    client = client_for(handle)
    result = await client.create_credential("argus-header-auth", "httpHeaderAuth", {"name": "X"})
    assert result is None


@pytest.mark.anyio
async def test_create_credential_raises_auth_error_on_401() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(401, {"message": "unauthorized"})

    client = client_for(handle)
    with pytest.raises(N8nAuthError):
        await client.create_credential("argus-header-auth", "httpHeaderAuth", {"name": "X"})


@pytest.mark.anyio
async def test_create_credential_returns_parsed_dict_on_200() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"id": "cred-1", "name": "argus-header-auth"})

    client = client_for(handle)
    result = await client.create_credential("argus-header-auth", "httpHeaderAuth", {"name": "X"})
    assert result == {"id": "cred-1", "name": "argus-header-auth"}


# --- stop_execution -----------------------------------------------------


@pytest.mark.parametrize("status", [404, 405])
@pytest.mark.anyio
async def test_stop_execution_degrades_quietly(status: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="not found")

    client = client_for(handle)
    # Must not raise.
    await client.stop_execution("exec-1")


@pytest.mark.anyio
async def test_stop_execution_raises_on_genuine_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(500, {"message": "boom"})

    client = client_for(handle)
    with pytest.raises(N8nUnavailable):
        await client.stop_execution("exec-1")


# --- fire -----------------------------------------------------------------


@pytest.mark.anyio
async def test_fire_returns_body_status_and_execution_id_from_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(500, {"message": "workflow failed", "executionId": "exec-999"})

    result = await fire(
        "http://localhost:5678/webhook/abc",
        {"foo": "bar"},
        transport=httpx.MockTransport(handle),
    )
    assert result.status_code == 500
    assert result.body == {"message": "workflow failed", "executionId": "exec-999"}
    assert result.execution_id == "exec-999"
    assert isinstance(result.elapsed_ms, int)


@pytest.mark.anyio
async def test_fire_returns_parsed_json_on_success() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"ok": True})

    result = await fire(
        "http://localhost:5678/webhook/abc",
        {"foo": "bar"},
        transport=httpx.MockTransport(handle),
    )
    assert result.status_code == 200
    assert result.body == {"ok": True}
    assert result.execution_id is None


@pytest.mark.anyio
async def test_fire_sends_basic_auth_and_no_api_key_header() -> None:
    seen_requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return json_response(200, {"ok": True})

    await fire(
        "http://localhost:5678/form/abc",
        {"foo": "bar"},
        basic_auth=("user", "pass"),
        transport=httpx.MockTransport(handle),
    )

    request = seen_requests[0]
    assert API_KEY_HEADER not in request.headers
    assert "authorization" in request.headers
    assert request.headers["authorization"].startswith("Basic ")


@pytest.mark.anyio
async def test_fire_raises_n8n_timeout_on_httpx_timeout() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(N8nTimeout):
        await fire(
            "http://localhost:5678/webhook/abc",
            {"foo": "bar"},
            transport=httpx.MockTransport(handle),
        )


# --- form_url / webhook_url ------------------------------------------------


def test_form_url_construction() -> None:
    assert form_url("http://localhost:5678", "abc-123") == "http://localhost:5678/form/abc-123"
    assert form_url("http://localhost:5678/", "abc-123") == "http://localhost:5678/form/abc-123"


def test_webhook_url_construction() -> None:
    assert webhook_url("http://localhost:5678", "my-hook") == "http://localhost:5678/webhook/my-hook"
    assert (
        webhook_url("http://localhost:5678/", "my-hook") == "http://localhost:5678/webhook/my-hook"
    )


# --- probe ------------------------------------------------------------------


@pytest.mark.anyio
async def test_probe_ok_with_latency() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": [{"id": "1"}]})

    result = await probe(
        "http://localhost:5678", API_KEY, transport=httpx.MockTransport(handle)
    )
    assert result.ok is True
    assert result.latency_ms is not None
    assert result.workflow_count == 1


@pytest.mark.anyio
async def test_probe_wrong_key() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return json_response(401, {"message": "unauthorized"})

    result = await probe(
        "http://localhost:5678", API_KEY, transport=httpx.MockTransport(handle)
    )
    assert result.ok is False
    assert "API key" in result.detail


@pytest.mark.anyio
async def test_probe_not_n8n_html_404() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html><body>Not Found</body></html>")

    result = await probe(
        "http://example.com", API_KEY, transport=httpx.MockTransport(handle)
    )
    assert result.ok is False
    assert "doesn't look like an n8n instance" in result.detail


@pytest.mark.anyio
async def test_probe_connection_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await probe(
        "http://localhost:5678", API_KEY, transport=httpx.MockTransport(handle)
    )
    assert result.ok is False
    assert "connection refused" in result.detail


@pytest.mark.anyio
async def test_probe_timeout() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    result = await probe(
        "http://localhost:5678", API_KEY, transport=httpx.MockTransport(handle)
    )
    assert result.ok is False
    assert "timed out" in result.detail
