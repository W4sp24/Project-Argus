"""Tests for POST /api/doctor and the /api/models registry (redesign §12/§7)."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def client(vault: Path) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault)))


def test_doctor_endpoint_reports_checks(client: TestClient) -> None:
    response = client.post("/api/doctor")
    assert response.status_code == 200
    checks = response.json()
    by_name = {check["name"]: check for check in checks}
    assert {"vault", "vault-git", "database", "chroma", "keyring"} <= set(by_name)
    assert all(check["status"] in ("OK", "WARN", "FAIL") for check in checks)
    assert by_name["vault"]["status"] == "OK"
    assert by_name["vault-git"]["status"] == "OK"


def test_models_defaults(client: TestClient) -> None:
    models = client.get("/api/models").json()
    by_name = {model["name"]: model for model in models}
    assert by_name["claude-sonnet-4"]["default"] is True
    assert by_name["claude-sonnet-4"]["builtin"] is True
    assert by_name["claude-haiku"]["provider"] == "anthropic"


def test_add_and_delete_local_model(client: TestClient, vault: Path) -> None:
    created = client.post(
        "/api/models", json={"name": "llama3", "endpoint": "http://localhost:11434/v1"}
    )
    assert created.status_code == 201
    assert created.json()["provider"] == "openai-compat"

    # Persists in the argus config dir (never the vault's note zones).
    models_file = vault / ".argus" / "models.json"
    assert models_file.is_file()
    assert json.loads(models_file.read_text(encoding="utf-8"))[0]["name"] == "llama3"

    listed = {model["name"] for model in client.get("/api/models").json()}
    assert "llama3" in listed

    deleted = client.delete("/api/models/llama3")
    assert deleted.status_code == 200
    assert "llama3" not in {model["name"] for model in client.get("/api/models").json()}


def test_add_model_validation(client: TestClient) -> None:
    ok = {"name": "llama3", "endpoint": "http://localhost:11434/v1"}
    assert client.post("/api/models", json=ok).status_code == 201
    assert client.post("/api/models", json=ok).status_code == 409, "duplicate name"
    assert (
        client.post(
            "/api/models", json={"name": "claude-sonnet-4", "endpoint": "http://x/v1"}
        ).status_code
        == 409
    ), "cannot shadow a built-in"
    assert (
        client.post("/api/models", json={"name": "bad", "endpoint": "not-a-url"}).status_code
        == 422
    )
    assert (
        client.post("/api/models", json={"name": "../evil", "endpoint": "http://x/v1"}).status_code
        == 422
    )


def test_delete_model_guards(client: TestClient) -> None:
    assert client.delete("/api/models/claude-sonnet-4").status_code == 400, "builtin protected"
    assert client.delete("/api/models/ghost").status_code == 404


def test_chat_agent_model_resolution(vault: Path) -> None:
    from backend.agent.runtime import MODEL, ChatAgent
    from backend.config import save_user_models

    settings = Settings(_vault_path=vault)
    agent = ChatAgent(settings)
    assert agent._resolve_model(None) == MODEL, "omitting model keeps today's behavior"
    assert agent._resolve_model("claude-haiku") == "claude-haiku"
    with pytest.raises(RuntimeError, match="unknown model"):
        agent._resolve_model("ghost")

    save_user_models(
        settings.models_file,
        [{"name": "llama3", "provider": "openai-compat", "endpoint": "http://localhost:11434/v1"}],
    )
    with pytest.raises(RuntimeError, match="preview"):
        agent._resolve_model("llama3")
