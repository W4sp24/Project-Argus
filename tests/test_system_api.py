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
    assert by_name["claude-sonnet-5"]["default"] is True
    assert by_name["claude-sonnet-5"]["builtin"] is True
    assert by_name["claude-haiku-4-5-20251001"]["provider"] == "anthropic"


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
            "/api/models", json={"name": "claude-sonnet-5", "endpoint": "http://x/v1"}
        ).status_code
        == 409
    ), "cannot shadow a built-in"
    assert (
        client.post("/api/models", json={"name": "bad", "endpoint": "not-a-url"}).status_code == 422
    )
    assert (
        client.post("/api/models", json={"name": "../evil", "endpoint": "http://x/v1"}).status_code
        == 422
    )


def test_delete_model_guards(client: TestClient) -> None:
    assert client.delete("/api/models/claude-sonnet-5").status_code == 400, "builtin protected"
    assert client.delete("/api/models/ghost").status_code == 404


def test_chat_agent_model_resolution(vault: Path) -> None:
    """Registered models resolve to the id that will actually run.

    Replaces the previous assertion that a non-anthropic provider raised
    "localModels is preview" — that rejection is exactly what this branch
    removes, so the test now pins the routing that took its place.
    """
    from backend.agent.runtime import MODEL, ChatAgent
    from backend.config import save_user_models

    settings = Settings(_vault_path=vault)
    agent = ChatAgent(settings)
    assert agent._resolve_model(None) == MODEL, "omitting model keeps today's behavior"
    assert agent._resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"
    with pytest.raises(RuntimeError, match="unknown model"):
        agent._resolve_model("ghost")

    save_user_models(
        settings.models_file,
        [
            {
                "name": "llama3",
                "provider": "openai-compat",
                "endpoint": "http://localhost:11434/v1",
            },
            {
                "name": "groq-llama",
                "provider": "openai-compat",
                "endpoint": "https://api.groq.com/openai/v1",
                "model_id": "llama-3.3-70b-versatile",
            },
        ],
    )
    assert agent._resolve_model("llama3") == "llama3", "local endpoints route for real now"
    assert agent._resolve_model("groq-llama") == "llama-3.3-70b-versatile", (
        "a display name may differ from the id the provider expects"
    )


def test_chat_agent_builds_the_right_adapter_per_provider(vault: Path) -> None:
    """Each registry provider maps to its own engine, from one selector."""
    from backend.agent.adapters import ClaudeSDKAdapter, resolve_adapter
    from backend.agent.anthropic_api import AnthropicAPIAdapter
    from backend.agent.credentials import KEYRING_SERVICE
    from backend.agent.openai_compat import OpenAICompatAdapter
    from backend.config import save_user_models

    settings = Settings(_vault_path=vault)
    save_user_models(
        settings.models_file,
        [
            {
                "name": "llama3",
                "provider": "openai-compat",
                "endpoint": "http://localhost:11434/v1",
            },
            {"name": "claude-key", "provider": "anthropic-api", "key_ref": "model:claude-key"},
        ],
    )

    # No model at all keeps the historical Claude Code path.
    assert isinstance(
        resolve_adapter(settings, None, fallback_model="claude-opus-4-8"), ClaudeSDKAdapter
    )
    assert isinstance(resolve_adapter(settings, "claude-sonnet-5"), ClaudeSDKAdapter)

    local = resolve_adapter(settings, "llama3")
    assert isinstance(local, OpenAICompatAdapter)
    assert local.endpoint == "http://localhost:11434/v1"
    assert local.api_key is None, "a local endpoint needs no credentials"

    import keyring

    keyring.set_password(KEYRING_SERVICE, "model:claude-key", "sk-test")
    try:
        hosted = resolve_adapter(settings, "claude-key")
        assert isinstance(hosted, AnthropicAPIAdapter)
        assert hosted.api_key == "sk-test", "the key comes from the keyring, never models.json"
    finally:
        keyring.delete_password(KEYRING_SERVICE, "model:claude-key")


def test_anthropic_api_model_without_a_stored_key_fails_readably(vault: Path) -> None:
    from backend.agent.adapters import AgentError, resolve_adapter
    from backend.config import save_user_models

    settings = Settings(_vault_path=vault)
    save_user_models(
        settings.models_file,
        [{"name": "keyless", "provider": "anthropic-api", "key_ref": "model:absent-on-purpose"}],
    )

    with pytest.raises(AgentError, match="no API key stored"):
        resolve_adapter(settings, "keyless")
