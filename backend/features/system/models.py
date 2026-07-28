"""The model registry endpoints (redesign §7).

Split out of the system router, which had grown to cover three unrelated
subjects; this is the largest of them by far. Built-ins come from
:mod:`backend.core.model_registry`, user-added models from ``.argus/models.json``
— never the vault, and never an API key (I4): keys live in the OS keyring and
only ``has_key: bool`` is ever returned.

Registering a non-Claude model runs a live tool-calling probe first, because
Argus has no no-tools fallback and a model that cannot call tools would
silently break citations (I6) and planner proposals (I1).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.agent import model_catalog
from backend.agent.adapters import (
    KNOWN_PROVIDERS,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_CLAUDE_CLI,
    PROVIDER_OPENAI_COMPAT,
    AgentError,
    ProbeResult,
    adapter_for_entry,
    entry_is_local,
    probe_tool_calling,
)
from backend.agent.credentials import CredentialError, delete_key, has_key, key_ref_for, store_key
from backend.agent.hardware import HardwareProfile, detect, ollama_base_url, ollama_models_dir
from backend.core.config import Settings
from backend.core.model_registry import (
    DEFAULT_MODELS,
    load_model_prefs,
    load_user_models,
    save_model_prefs,
    save_user_models,
)

MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

#: The models Argus ships with. Membership here — not a guess from the shape of
#: an entry — is what makes a model undeletable.
BUILTIN_MODEL_NAMES = frozenset(entry["name"] for entry in DEFAULT_MODELS)

#: Kept exact: `web/e2e/system.spec.ts` asserts a user sees this wording.
UNREACHABLE_DETAIL = "could not reach that endpoint; check the URL and that the server is running"

# A pull can take many minutes on a slow connection; the stream itself is the
# progress indicator, so only a stall should end it.
PULL_TIMEOUT_SECONDS = 3600.0


class ModelInfo(BaseModel):
    """One registry entry. ``builtin`` models cannot be deleted.

    ``local`` drives the LOCAL/HOSTED badge — whether the user's notes stay on
    this machine. ``has_key`` says a credential exists without ever exposing
    it (I4).
    """

    name: str
    provider: str  # "anthropic" | "anthropic-api" | "openai-compat"
    endpoint: str | None = None
    key_ref: str | None = None  # keyring reference only — never a secret (I4)
    model_id: str | None = None
    default: bool = False
    builtin: bool = False
    local: bool = False
    has_key: bool = False


class AddModelRequest(BaseModel):
    """Register a model.

    The historical body — ``{name, endpoint}`` — still means "an
    OpenAI-compatible endpoint", so older clients keep working unchanged.
    """

    name: str
    endpoint: str | None = None
    provider: str = PROVIDER_OPENAI_COMPAT
    api_key: str | None = None
    model_id: str | None = None
    # Registration verifies tool calling by default. Turning it off is a
    # deliberate escape hatch for adding a model whose endpoint is not up yet
    # (a hosted key configured offline), not a way around the requirement —
    # the model still has to call tools when it actually runs.
    verify: bool = True


class TestModelRequest(BaseModel):
    """Check a configuration without saving it — the UI's Test button."""

    provider: str = PROVIDER_OPENAI_COMPAT
    endpoint: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    name: str | None = None


class TestModelResponse(BaseModel):
    """What the Test button renders."""

    ok: bool
    detail: str
    tool_calling: bool = False
    latency_ms: int = 0
    available_models: list[str] = []


class DefaultModelRequest(BaseModel):
    name: str


class InstallRequest(BaseModel):
    name: str


class CatalogEntryInfo(BaseModel):
    """One catalog model, scored against the detected machine."""

    name: str
    label: str
    parameters: str
    size_gb: float
    summary: str
    tool_calling: bool
    min_ram_gb: float
    min_vram_gb: float
    verdict: str  # fits | slow | insufficient | unknown
    reason: str
    installed: bool = False


class HardwareInfo(BaseModel):
    """What Argus could detect. ``None`` means unknown, never zero."""

    ram_gb: float | None = None
    vram_gb: float | None = None
    gpu_name: str | None = None
    platform: str
    ollama_url: str
    ollama_models_dir: str


class CatalogResponse(BaseModel):
    hardware: HardwareInfo
    recommended: str | None = None
    models: list[CatalogEntryInfo]


Prober = Callable[[dict[str, Any], str | None], Any]
Puller = Callable[[str], AsyncIterator[dict[str, Any]]]


async def _default_prober(entry: dict[str, Any], api_key: str | None = None) -> ProbeResult:
    """Run a real tool-calling probe against one prospective registry entry.

    ``api_key`` carries a key the user has typed but not yet saved, so a
    configuration can be verified before anything is written anywhere.
    """
    try:
        adapter = adapter_for_entry(entry, api_key=api_key)
    except AgentError as exc:
        return ProbeResult(ok=False, detail=str(exc))
    return await probe_tool_calling(adapter)


async def _default_puller(name: str) -> AsyncIterator[dict[str, Any]]:
    """Stream ``ollama pull`` progress from Ollama's HTTP API.

    The HTTP API rather than the CLI: it emits structured NDJSON progress
    instead of ANSI-decorated terminal output, and it works when Ollama runs as
    a background service without its CLI on PATH — which is the normal state
    after the Windows installer.
    """
    import httpx

    url = f"{ollama_base_url()}/api/pull"
    async with (
        httpx.AsyncClient(timeout=PULL_TIMEOUT_SECONDS) as client,
        client.stream("POST", url, json={"model": name, "stream": True}) as response,
    ):
        if response.status_code >= 400:
            await response.aread()
            raise AgentError(f"Ollama refused the download: {response.text.strip()}")
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def build_models_router(
    settings: Settings,
    prober: Prober | None = None,
    puller: Puller | None = None,
) -> APIRouter:
    """The /api/models routes.

    No prefix of its own: the system router mounts this under its ``/api``.

    ``prober`` and ``puller`` are injectable for the same reason the chat
    runner and generator are: they make live network calls, and tests must not.
    """
    router = APIRouter()
    probe = prober or _default_prober
    pull = puller or _default_puller

    def _registry() -> list[ModelInfo]:
        return [
            ModelInfo(
                name=entry["name"],
                provider=entry["provider"],
                endpoint=entry.get("endpoint"),
                key_ref=entry.get("key_ref"),
                model_id=entry.get("model_id"),
                default=entry.get("default", False),
                # Membership in DEFAULT_MODELS, not a shape heuristic. Deriving
                # it from `provider == anthropic and no endpoint` meant a model
                # the *user* added with provider "anthropic" was reported
                # built-in and could then never be deleted — 400 forever, only
                # recoverable by hand-editing models.json.
                builtin=entry["name"] in BUILTIN_MODEL_NAMES,
                local=entry_is_local(entry),
                has_key=has_key(entry.get("key_ref")),
            )
            for entry in settings.models
        ]

    @router.get("/models", response_model=list[ModelInfo])
    def list_models() -> list[ModelInfo]:
        return _registry()

    @router.get("/models/hardware", response_model=HardwareInfo)
    def model_hardware() -> HardwareInfo:
        return _hardware_info(detect())

    @router.get("/models/catalog", response_model=CatalogResponse)
    def model_catalog_endpoint() -> CatalogResponse:
        profile = detect()
        registered = {entry["name"] for entry in settings.models}
        entries = [
            CatalogEntryInfo(
                name=item.model.name,
                label=item.model.label,
                parameters=item.model.parameters,
                size_gb=item.model.size_gb,
                summary=item.model.summary,
                tool_calling=item.model.tool_calling,
                min_ram_gb=item.model.min_ram_gb,
                min_vram_gb=item.model.min_vram_gb,
                verdict=item.verdict,
                reason=item.reason,
                installed=item.model.name in registered,
            )
            for item in model_catalog.annotated_catalog(profile)
        ]
        pick = model_catalog.recommended(profile)
        return CatalogResponse(
            hardware=_hardware_info(profile),
            recommended=pick.name if pick else None,
            models=entries,
        )

    @router.post("/models/test", response_model=TestModelResponse)
    async def test_model(request: TestModelRequest) -> TestModelResponse:
        """Verify a configuration before saving it.

        This is what makes the config UI usable by a non-developer: they find
        out the endpoint is wrong, the key is rejected, or the model cannot
        call tools *now*, with a readable reason, rather than during their
        first real question.
        """
        provider = _validated_provider(request.provider)
        entry = {
            "name": request.name or request.model_id or "probe",
            "provider": provider,
            "endpoint": request.endpoint,
            "model_id": request.model_id,
        }

        available, reason = await _list_available(provider, request.endpoint, request.api_key)

        if not request.model_id:
            # Nothing to probe yet — the user is still discovering what the
            # endpoint serves, which is itself a useful "is this reachable?".
            reachable = available is not None
            return TestModelResponse(
                ok=reachable,
                detail="reachable — now choose a model" if reachable else reason,
                available_models=available or [],
            )

        # The typed-but-unsaved key travels in memory for this one call only.
        result = await probe(entry, request.api_key)
        return TestModelResponse(
            ok=result.ok,
            detail=result.detail,
            tool_calling=result.tool_calling,
            latency_ms=result.latency_ms,
            available_models=available or [],
        )

    @router.post("/models", response_model=ModelInfo, status_code=201)
    async def add_model(request: AddModelRequest) -> ModelInfo:
        name = request.name.strip()
        if not MODEL_NAME_RE.match(name):
            raise HTTPException(status_code=422, detail="invalid model name")
        provider = _validated_provider(request.provider)

        endpoint = (request.endpoint or "").strip() or None
        if provider == PROVIDER_OPENAI_COMPAT:
            if not endpoint or not endpoint.startswith(("http://", "https://")):
                raise HTTPException(status_code=422, detail="endpoint must be an http(s) URL")
        elif endpoint and not endpoint.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="endpoint must be an http(s) URL")

        if provider == PROVIDER_ANTHROPIC_API and not request.api_key:
            raise HTTPException(status_code=422, detail="an Anthropic API model needs an API key")

        if any(model.name == name for model in _registry()):
            raise HTTPException(status_code=409, detail=f"model {name} already exists")

        entry: dict[str, Any] = {
            "name": name,
            "provider": provider,
            "endpoint": endpoint,
            "model_id": (request.model_id or "").strip() or None,
        }

        if request.verify:
            result = await probe(entry, request.api_key)
            if not result.ok:
                raise HTTPException(
                    status_code=422, detail=f"{name} did not pass the check: {result.detail}"
                )

        # The key is stored only once the model is known to work, so a failed
        # registration leaves nothing behind in the keyring.
        if request.api_key:
            ref = key_ref_for(name)
            try:
                store_key(ref, request.api_key)
            except CredentialError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            entry["key_ref"] = ref

        user_models = load_user_models(settings.models_file)
        user_models.append({k: v for k, v in entry.items() if v is not None})
        save_user_models(settings.models_file, user_models)
        return next(model for model in _registry() if model.name == name)

    @router.post("/models/default", response_model=ModelInfo)
    def set_default_model(request: DefaultModelRequest) -> ModelInfo:
        registry = {model.name: model for model in _registry()}
        if request.name not in registry:
            raise HTTPException(status_code=404, detail=f"no model {request.name}")
        prefs = load_model_prefs(settings.model_prefs_file)
        prefs["default"] = request.name
        save_model_prefs(settings.model_prefs_file, prefs)
        return next(model for model in _registry() if model.name == request.name)

    @router.post("/models/install")
    async def install_model(request: InstallRequest) -> StreamingResponse:
        """Download a catalog model through Ollama, streaming progress.

        NDJSON rather than a WebSocket: a download is a one-shot request/
        response with no client-to-server traffic, and NDJSON stays testable
        through TestClient without a socket.
        """
        model = model_catalog.find(request.name)
        if model is None:
            raise HTTPException(
                status_code=404,
                detail=f"{request.name} is not in the catalog of tool-calling models",
            )
        if any(entry.name == request.name for entry in _registry()):
            raise HTTPException(status_code=409, detail=f"model {request.name} already exists")

        async def stream() -> AsyncIterator[bytes]:
            # Two failure domains, reported separately. Registration used to sit
            # inside this try, so a failed models.json write was reported as
            # "is Ollama installed and running?" — which is both wrong and
            # unactionable when the download plainly just succeeded.
            try:
                async for event in pull(request.name):
                    yield _ndjson({"type": "progress", **event})
            except AgentError as exc:
                yield _ndjson({"type": "error", "detail": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - the client sees the reason
                yield _ndjson(
                    {
                        "type": "error",
                        "detail": (
                            f"{exc} — is Ollama installed and running? "
                            f"Argus looked at {ollama_base_url()}"
                        ),
                    }
                )
                return

            try:
                _register_pulled(request.name)
            except Exception as exc:  # noqa: BLE001 - the client sees the reason
                yield _ndjson(
                    {
                        "type": "error",
                        "detail": (
                            f"{request.name} downloaded, but Argus could not add it to the "
                            f"model list: {exc}"
                        ),
                    }
                )
                return
            yield _ndjson({"type": "done", "name": request.name})

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    def _register_pulled(name: str) -> None:
        """Add a freshly pulled Ollama model to the registry, ready to use."""
        user_models = load_user_models(settings.models_file)
        if any(entry.get("name") == name for entry in user_models):
            return
        user_models.append(
            {
                "name": name,
                "provider": PROVIDER_OPENAI_COMPAT,
                "endpoint": f"{ollama_base_url()}/v1",
            }
        )
        save_user_models(settings.models_file, user_models)

    @router.delete("/models/{name}")
    def delete_model(name: str) -> dict[str, str]:
        registry = {model.name: model for model in _registry()}
        model = registry.get(name)
        if model is None:
            raise HTTPException(status_code=404, detail=f"no model {name}")
        if model.builtin:
            raise HTTPException(status_code=400, detail="built-in models cannot be removed")
        remaining = [m for m in load_user_models(settings.models_file) if m.get("name") != name]
        save_user_models(settings.models_file, remaining)
        # Leaving an orphaned secret behind would outlive the thing it
        # belonged to, so the keyring entry goes with the model.
        delete_key(model.key_ref)
        prefs = load_model_prefs(settings.model_prefs_file)
        if prefs.get("default") == name:
            prefs.pop("default", None)
            save_model_prefs(settings.model_prefs_file, prefs)
        return {"status": "deleted", "name": name}

    return router


def _ndjson(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


def _hardware_info(profile: HardwareProfile) -> HardwareInfo:
    return HardwareInfo(
        ram_gb=profile.ram_gb,
        vram_gb=profile.vram_gb,
        gpu_name=profile.gpu_name,
        platform=profile.platform,
        ollama_url=ollama_base_url(),
        ollama_models_dir=ollama_models_dir(),
    )


def _validated_provider(provider: str) -> str:
    value = (provider or "").strip() or PROVIDER_OPENAI_COMPAT
    if value not in KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown provider {value!r} (expected one of {', '.join(KNOWN_PROVIDERS)})",
        )
    return value


async def _list_available(
    provider: str, endpoint: str | None, api_key: str | None
) -> tuple[list[str] | None, str]:
    """Model ids an endpoint serves, and why not when it cannot be reached.

    The reason is carried rather than collapsed. A rejected API key, a name
    that does not resolve and a server that never answers are three different
    problems with three different fixes, and reporting all of them as "check
    that the server is running" sends someone who mistyped a key off to restart
    a server that was fine all along. This is the front door of the guided
    setup flow and the one screen a non-developer is alone on.
    """
    try:
        if provider == PROVIDER_OPENAI_COMPAT:
            if not endpoint:
                return None, "enter the server's address first"
            from backend.agent.openai_compat import list_models

            return await asyncio.wait_for(list_models(endpoint, api_key), timeout=15), ""
        if provider == PROVIDER_ANTHROPIC_API:
            if not api_key:
                return None, "paste an API key first"
            from backend.agent.anthropic_api import DEFAULT_ENDPOINT, list_models

            return (
                await asyncio.wait_for(
                    list_models(api_key, endpoint or DEFAULT_ENDPOINT), timeout=15
                ),
                "",
            )
        if provider == PROVIDER_CLAUDE_CLI:
            return None, (
                "Claude Code has nothing to connect to — it runs through your signed-in CLI"
            )
    except TimeoutError:
        return None, "the endpoint did not answer within 15 seconds"
    except AgentError as exc:
        # The provider's own words: "401 … — check the API key", "404 … is the
        # model pulled on that endpoint?". Far better than anything we'd invent.
        return None, str(exc)
    except Exception:  # noqa: BLE001 - unreachable is a normal answer here
        return None, UNREACHABLE_DETAIL
    return None, UNREACHABLE_DETAIL
