"""Tests for POST /api/doctor and the /api/models registry (redesign §12/§7)."""

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent.adapters import ProbeResult
from backend.core.config import Settings
from backend.main import create_app


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    return root


async def passing_prober(entry: dict, api_key: str | None = None) -> ProbeResult:
    """A model that answers and calls tools — the happy path."""
    return ProbeResult(ok=True, detail="ok", tool_calling=True, latency_ms=12)


async def failing_prober(entry: dict, api_key: str | None = None) -> ProbeResult:
    """A model that will not call tools — Argus must refuse to register it."""
    return ProbeResult(ok=False, detail="never called the test tool")


@pytest.fixture()
def client(vault: Path) -> TestClient:
    """Registration probes a live endpoint, so tests inject a fake prober.

    Same reasoning as the injected chat runner and generator: the real one
    makes a network call, and the suite must not.
    """
    return TestClient(create_app(Settings(_vault_path=vault), model_prober=passing_prober))


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


# --- capability probe -------------------------------------------------------


def test_registration_rejects_a_model_that_will_not_call_tools(vault: Path) -> None:
    """No no-tools fallback exists, so this would silently break I6 and I1."""
    app = create_app(Settings(_vault_path=vault), model_prober=failing_prober)
    client = TestClient(app)

    response = client.post(
        "/api/models", json={"name": "chatty", "endpoint": "http://localhost:11434/v1"}
    )

    assert response.status_code == 422
    assert "never called the test tool" in response.json()["detail"]
    assert "chatty" not in {m["name"] for m in client.get("/api/models").json()}
    assert not (vault / ".argus" / "models.json").exists(), "a rejected model persists nothing"


def test_verify_false_skips_the_probe_for_offline_registration(vault: Path) -> None:
    """Adding a hosted model before its endpoint is up is legitimate."""
    client = TestClient(create_app(Settings(_vault_path=vault), model_prober=failing_prober))

    created = client.post(
        "/api/models",
        json={"name": "later", "endpoint": "https://api.groq.com/openai/v1", "verify": False},
    )

    assert created.status_code == 201


def test_probe_runs_before_anything_is_written(vault: Path) -> None:
    """A failed registration must not leave a key behind in the keyring."""
    from backend.agent.credentials import has_key, key_ref_for

    client = TestClient(create_app(Settings(_vault_path=vault), model_prober=failing_prober))

    client.post(
        "/api/models",
        json={"name": "doomed", "provider": "anthropic-api", "api_key": "sk-would-be-orphaned"},
    )

    assert not has_key(key_ref_for("doomed"))


# --- test-connection --------------------------------------------------------


def test_test_endpoint_reports_a_verdict_without_saving(client: TestClient) -> None:
    response = client.post(
        "/api/models/test",
        json={
            "provider": "openai-compat",
            "endpoint": "http://localhost:11434/v1",
            "model_id": "llama3.1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["tool_calling"] is True
    assert client.get("/api/models").json() == client.get("/api/models").json()
    assert "llama3.1" not in {m["name"] for m in client.get("/api/models").json()}


def test_test_endpoint_surfaces_a_failure_reason(vault: Path) -> None:
    client = TestClient(create_app(Settings(_vault_path=vault), model_prober=failing_prober))

    payload = client.post(
        "/api/models/test",
        json={"endpoint": "http://localhost:11434/v1", "model_id": "tiny"},
    ).json()

    assert payload["ok"] is False
    assert "never called the test tool" in payload["detail"]


def test_test_endpoint_rejects_an_unknown_provider(client: TestClient) -> None:
    response = client.post("/api/models/test", json={"provider": "wishful-thinking"})
    assert response.status_code == 422


# --- credentials ------------------------------------------------------------


def test_api_key_goes_to_the_keyring_never_to_models_json(client: TestClient, vault: Path) -> None:
    from backend.agent.credentials import delete_key, get_key, key_ref_for

    created = client.post(
        "/api/models",
        json={
            "name": "groq-llama",
            "endpoint": "https://api.groq.com/openai/v1",
            "api_key": "sk-super-secret",
            "model_id": "llama-3.3-70b-versatile",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["has_key"] is True
    assert body["local"] is False, "a hosted endpoint is badged as leaving the machine"
    assert "sk-super-secret" not in json.dumps(body), "no endpoint ever returns a key"

    try:
        on_disk = (vault / ".argus" / "models.json").read_text(encoding="utf-8")
        assert "sk-super-secret" not in on_disk, "the key must never reach disk (I4)"
        assert key_ref_for("groq-llama") in on_disk, "only the reference is stored"
        assert get_key(key_ref_for("groq-llama")) == "sk-super-secret"

        client.delete("/api/models/groq-llama")
        assert get_key(key_ref_for("groq-llama")) is None, "deleting a model takes its key too"
    finally:
        delete_key(key_ref_for("groq-llama"))


def test_anthropic_api_model_requires_a_key(client: TestClient) -> None:
    response = client.post("/api/models", json={"name": "claude-key", "provider": "anthropic-api"})
    assert response.status_code == 422
    assert "API key" in response.json()["detail"]


def test_local_endpoints_are_badged_local(client: TestClient) -> None:
    client.post("/api/models", json={"name": "llama3", "endpoint": "http://localhost:11434/v1"})
    entry = next(m for m in client.get("/api/models").json() if m["name"] == "llama3")
    assert entry["local"] is True
    assert entry["has_key"] is False


# --- default model ----------------------------------------------------------


def test_setting_the_default_moves_the_flag(client: TestClient) -> None:
    client.post("/api/models", json={"name": "llama3", "endpoint": "http://localhost:11434/v1"})

    response = client.post("/api/models/default", json={"name": "llama3"})
    assert response.status_code == 200

    defaults = [m["name"] for m in client.get("/api/models").json() if m["default"]]
    assert defaults == ["llama3"], "exactly one default, and it is the chosen one"


def test_setting_an_unknown_default_is_404(client: TestClient) -> None:
    assert client.post("/api/models/default", json={"name": "ghost"}).status_code == 404


def test_deleting_the_default_restores_the_builtin(client: TestClient) -> None:
    """Removing your default must not leave every model-less caller broken."""
    client.post("/api/models", json={"name": "llama3", "endpoint": "http://localhost:11434/v1"})
    client.post("/api/models/default", json={"name": "llama3"})
    client.delete("/api/models/llama3")

    defaults = [m["name"] for m in client.get("/api/models").json() if m["default"]]
    assert defaults == ["claude-sonnet-5"]


# --- hardware + catalog -----------------------------------------------------


def test_hardware_endpoint_reports_what_it_could_detect(client: TestClient) -> None:
    payload = client.get("/api/models/hardware").json()
    assert payload["ram_gb"] is not None, "RAM is detectable on every supported platform"
    assert payload["ollama_url"].startswith("http")
    assert payload["ollama_models_dir"]


def test_catalog_scores_every_model_against_this_machine(client: TestClient) -> None:
    payload = client.get("/api/models/catalog").json()

    assert payload["models"], "the catalog is not empty"
    assert all(entry["tool_calling"] for entry in payload["models"])
    assert all(
        entry["verdict"] in ("fits", "slow", "insufficient", "unknown")
        for entry in payload["models"]
    )
    assert all(entry["reason"] for entry in payload["models"])
    assert payload["hardware"]["ram_gb"] is not None


def test_catalog_marks_already_registered_models_as_installed(client: TestClient) -> None:
    name = client.get("/api/models/catalog").json()["models"][0]["name"]
    client.post(
        "/api/models",
        json={"name": name, "endpoint": "http://localhost:11434/v1"},
    )

    entry = next(e for e in client.get("/api/models/catalog").json()["models"] if e["name"] == name)
    assert entry["installed"] is True


# --- install ----------------------------------------------------------------


async def fake_puller(name: str) -> AsyncIterator[dict]:
    yield {"status": "pulling manifest"}
    yield {"status": "downloading", "completed": 500, "total": 1000}
    yield {"status": "success"}


async def broken_puller(name: str) -> AsyncIterator[dict]:
    raise RuntimeError("connection refused")
    yield  # pragma: no cover


def ndjson_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_install_streams_progress_then_registers_the_model(vault: Path) -> None:
    app = create_app(
        Settings(_vault_path=vault), model_prober=passing_prober, model_puller=fake_puller
    )
    client = TestClient(app)
    name = client.get("/api/models/catalog").json()["models"][0]["name"]

    response = client.post("/api/models/install", json={"name": name})
    assert response.status_code == 200
    events = ndjson_lines(response.text)

    assert [event["type"] for event in events][-1] == "done"
    assert any(event.get("status") == "downloading" for event in events), "progress is streamed"

    # A pulled model lands in the registry ready to use — no second "add" step.
    registered = {m["name"]: m for m in client.get("/api/models").json()}
    assert name in registered
    assert registered[name]["local"] is True


def test_install_probes_tool_calling_instead_of_trusting_the_catalog(vault: Path) -> None:
    """The catalog's ``tool_calling: true`` is a hand-written literal, not a fact.

    ``add_model`` gates on a live probe; the download path did not, so a model
    whose catalog entry was wrong for a given tag or quantization only revealed
    itself at the user's first question — answered with no citations, which is
    precisely what the probe exists to prevent.
    """
    probed: list[str] = []

    async def recording_prober(entry, api_key=None):
        probed.append(entry["name"])
        return ProbeResult(ok=True, detail="called the probe tool", tool_calling=True)

    app = create_app(
        Settings(_vault_path=vault), model_prober=recording_prober, model_puller=fake_puller
    )
    client = TestClient(app)
    name = client.get("/api/models/catalog").json()["models"][0]["name"]

    events = ndjson_lines(client.post("/api/models/install", json={"name": name}).text)

    assert probed == [name], "a freshly pulled model must be measured, not assumed"
    verified = next(event for event in events if event["type"] == "verified")
    assert verified["tool_calling"] is True
    assert events[-1]["type"] == "done"


def test_a_pulled_model_that_fails_the_probe_is_kept_but_flagged(vault: Path) -> None:
    """The user already paid for the download — flag it, do not discard it."""

    async def failing(entry, api_key=None):
        return ProbeResult(ok=False, detail="answered but never called the tool")

    app = create_app(Settings(_vault_path=vault), model_prober=failing, model_puller=fake_puller)
    client = TestClient(app)
    name = client.get("/api/models/catalog").json()["models"][0]["name"]

    events = ndjson_lines(client.post("/api/models/install", json={"name": name}).text)

    verified = next(event for event in events if event["type"] == "verified")
    assert verified["tool_calling"] is False
    assert "never called the tool" in verified["detail"]
    assert name in {m["name"] for m in client.get("/api/models").json()}, (
        "a multi-gigabyte download is not thrown away over a failed probe"
    )


def test_install_reports_a_readable_error_when_ollama_is_down(vault: Path) -> None:
    app = create_app(
        Settings(_vault_path=vault), model_prober=passing_prober, model_puller=broken_puller
    )
    client = TestClient(app)
    name = client.get("/api/models/catalog").json()["models"][0]["name"]

    events = ndjson_lines(client.post("/api/models/install", json={"name": name}).text)

    assert events[-1]["type"] == "error"
    assert "Ollama" in events[-1]["detail"], "the error names the thing to go fix"
    assert name not in {m["name"] for m in client.get("/api/models").json()}


def test_install_refuses_models_outside_the_curated_catalog(vault: Path) -> None:
    """Off-catalog models have no tool-calling guarantee, which is the whole point."""
    app = create_app(Settings(_vault_path=vault), model_puller=fake_puller)
    client = TestClient(app)

    response = client.post("/api/models/install", json={"name": "some-random:70b"})
    assert response.status_code == 404
    assert "catalog" in response.json()["detail"]


def test_doctor_reports_ollama_without_failing_the_install(client: TestClient) -> None:
    """Ollama is one option among several, so its absence is WARN, never FAIL."""
    checks = {check["name"]: check for check in client.post("/api/doctor").json()}
    assert "ollama" in checks
    assert checks["ollama"]["status"] in ("OK", "WARN")


def test_chat_agent_model_resolution(vault: Path) -> None:
    """Registered models resolve to the id that will actually run.

    Replaces the previous assertion that a non-anthropic provider raised
    "localModels is preview" — that rejection is exactly what this branch
    removes, so the test now pins the routing that took its place.
    """
    from backend.agent.runtime import MODEL, ChatAgent
    from backend.core.model_registry import save_user_models

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
    from backend.core.model_registry import save_user_models

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
    from backend.core.model_registry import save_user_models

    settings = Settings(_vault_path=vault)
    save_user_models(
        settings.models_file,
        [{"name": "keyless", "provider": "anthropic-api", "key_ref": "model:absent-on-purpose"}],
    )

    with pytest.raises(AgentError, match="no API key stored"):
        resolve_adapter(settings, "keyless")
