"""Tests for the integrations registry and /api/integrations."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.connectors import gcal, todoist
from backend.core.config import Settings
from backend.features.integrations import store
from backend.features.integrations.mcp_client import McpProbeResult
from backend.features.integrations.router import build_integrations_router
from backend.main import create_app
from fastapi import FastAPI


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault)


def _client(settings: Settings, prober=None) -> TestClient:
    app = FastAPI()
    app.include_router(build_integrations_router(settings, prober))
    return TestClient(app)


def _ok_prober(tools: list[str]):
    async def prober(entry, token):  # noqa: ANN001 - test double
        return McpProbeResult(ok=True, detail="connected", latency_ms=7, tools=tools)

    return prober


def _failing_prober(detail: str):
    async def prober(entry, token):  # noqa: ANN001 - test double
        return McpProbeResult(ok=False, detail=detail)

    return prober


# --- registry -------------------------------------------------------------


def test_load_servers_tolerates_missing_and_corrupt(tmp_path: Path) -> None:
    assert store.load_servers(tmp_path / "absent.json") == []
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{{{ not json", encoding="utf-8")
    assert store.load_servers(corrupt) == []


def test_list_integrations_reports_connector_state(settings: Settings, monkeypatch) -> None:
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(todoist, "configured", lambda: True)

    payload = _client(settings).get("/integrations").json()
    by_id = {c["id"]: c for c in payload["connectors"]}

    assert by_id["todoist"]["status"] == "wired"
    # No OAuth client uploaded yet, so gcal is not merely "not connected".
    assert by_id["gcal"]["status"] == "needs-credentials"
    assert payload["mcp_servers"] == []


# --- MCP registry ---------------------------------------------------------


def test_add_mcp_server_persists_and_lists_tools(settings: Settings) -> None:
    client = _client(settings, _ok_prober(["search", "fetch"]))

    created = client.post(
        "/integrations/mcp",
        json={"name": "docs", "transport": "stdio", "command": "python", "args": ["-m", "srv"]},
    )
    assert created.status_code == 201
    assert created.json()["tools"] == ["search", "fetch"]

    listed = client.get("/integrations").json()["mcp_servers"]
    assert [s["name"] for s in listed] == ["docs"]
    assert listed[0]["args"] == ["-m", "srv"]

    on_disk = json.loads(settings.mcp_servers_file.read_text(encoding="utf-8"))
    assert on_disk[0]["command"] == "python"


def test_failed_probe_blocks_registration(settings: Settings) -> None:
    client = _client(settings, _failing_prober("command not found"))

    response = client.post(
        "/integrations/mcp", json={"name": "broken", "command": "nope"}
    )
    assert response.status_code == 422
    assert "command not found" in response.json()["detail"]
    assert not settings.mcp_servers_file.exists(), "nothing is written when the probe fails"


def test_duplicate_server_is_rejected(settings: Settings) -> None:
    client = _client(settings, _ok_prober([]))
    body = {"name": "docs", "command": "python"}
    assert client.post("/integrations/mcp", json=body).status_code == 201
    assert client.post("/integrations/mcp", json=body).status_code == 409


def test_invalid_server_requests_are_rejected(settings: Settings) -> None:
    client = _client(settings, _ok_prober([]))

    assert client.post("/integrations/mcp", json={"name": "bad name!"}).status_code == 422
    assert (
        client.post("/integrations/mcp", json={"name": "ok", "transport": "carrier-pigeon"}).status_code
        == 422
    )
    # stdio with no command, and http with a non-URL.
    assert client.post("/integrations/mcp", json={"name": "ok"}).status_code == 422
    assert (
        client.post(
            "/integrations/mcp", json={"name": "ok", "transport": "http", "url": "ftp://x"}
        ).status_code
        == 422
    )


def test_token_never_comes_back_over_the_api(settings: Settings, monkeypatch) -> None:
    """I4: a stored bearer is reported as has_key, never returned."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "backend.features.integrations.router.store_key",
        lambda ref, key: stored.__setitem__(ref, key),
    )
    monkeypatch.setattr("backend.features.integrations.router.has_key", lambda ref: ref in stored)

    client = _client(settings, _ok_prober([]))
    created = client.post(
        "/integrations/mcp",
        json={"name": "remote", "transport": "http", "url": "https://x/mcp", "token": "s3cret"},
    )
    assert created.status_code == 201
    assert created.json()["has_key"] is True
    assert "s3cret" not in created.text

    on_disk = settings.mcp_servers_file.read_text(encoding="utf-8")
    assert "s3cret" not in on_disk, "the secret must live only in the keyring"
    assert stored == {"mcp:remote": "s3cret"}


def test_delete_removes_server_and_its_key(settings: Settings, monkeypatch) -> None:
    deleted: list[str | None] = []
    monkeypatch.setattr(
        "backend.features.integrations.router.delete_key", lambda ref: deleted.append(ref)
    )
    monkeypatch.setattr("backend.features.integrations.router.store_key", lambda ref, key: None)

    client = _client(settings, _ok_prober([]))
    client.post(
        "/integrations/mcp",
        json={"name": "remote", "transport": "http", "url": "https://x/mcp", "token": "t"},
    )

    assert client.delete("/integrations/mcp/remote").status_code == 200
    assert client.get("/integrations").json()["mcp_servers"] == []
    assert deleted == ["mcp:remote"]

    assert client.delete("/integrations/mcp/ghost").status_code == 404


def test_test_endpoint_saves_nothing(settings: Settings) -> None:
    client = _client(settings, _ok_prober(["a"]))
    response = client.post("/integrations/mcp/test", json={"name": "docs", "command": "python"})
    assert response.json() == {"ok": True, "detail": "connected", "latency_ms": 7, "tools": ["a"]}
    assert not settings.mcp_servers_file.exists()


# --- connectors -----------------------------------------------------------


def test_gcal_credentials_rejects_non_oauth_json(settings: Settings) -> None:
    client = _client(settings)

    assert (
        client.post("/integrations/gcal/credentials", json={"credentials_json": "nope"}).status_code
        == 422
    )
    response = client.post(
        "/integrations/gcal/credentials", json={"credentials_json": '{"hello": 1}'}
    )
    assert response.status_code == 422
    assert "Desktop" in response.json()["detail"]


def test_gcal_credentials_are_saved_outside_the_vault(settings: Settings) -> None:
    client = _client(settings)
    payload = json.dumps({"installed": {"client_id": "x", "client_secret": "y"}})

    assert (
        client.post("/integrations/gcal/credentials", json={"credentials_json": payload}).status_code
        == 200
    )
    assert settings.gcal_credentials_file.is_file()
    assert settings.gcal_credentials_file.parent.name == ".argus"

    # And the status now advances from needs-credentials to not-connected.
    by_id = {c["id"]: c for c in client.get("/integrations").json()["connectors"]}
    assert by_id["gcal"]["status"] == "not-connected"


def test_gcal_connect_without_credentials_is_a_conflict(settings: Settings, monkeypatch) -> None:
    monkeypatch.setattr(gcal, "CREDENTIALS_FILE", settings.vault_path / "absent.json")
    assert _client(settings).post("/integrations/gcal/connect").status_code == 409


def test_todoist_connect_verifies_before_storing(settings: Settings, monkeypatch) -> None:
    stored: list[str] = []
    monkeypatch.setattr(todoist, "connect", lambda token: stored.append(token))

    def reject(_api=None):  # noqa: ANN001 - test double
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(todoist, "list_tasks", reject)
    client = _client(settings)

    response = client.post("/integrations/todoist/connect", json={"token": "bad"})
    # Without the optional client library installed the call cannot be verified,
    # which is itself a valid outcome — but it must never be silently "connected".
    if response.status_code == 422:
        assert "rejected" in response.json()["detail"]
        assert stored == [], "a rejected token is not stored"
    else:
        assert "not verified" in response.json()["detail"]

    assert client.post("/integrations/todoist/connect", json={"token": "  "}).status_code == 422


def test_disconnect_unknown_connector_is_404(settings: Settings) -> None:
    assert _client(settings).delete("/integrations/nonsense").status_code == 404


# --- wiring ---------------------------------------------------------------


def test_routes_are_mounted_on_the_real_app(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault)))

    payload = client.get("/api/integrations").json()
    assert "connectors" in payload and "mcp_servers" in payload

    snippets = client.get("/api/integrations/mcp/snippets").json()
    assert set(snippets) == {"claude-code", "codex-cli", "gemini-cli"}
