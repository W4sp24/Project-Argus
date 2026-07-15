"""SYSTEM tab endpoints (redesign §12/§14/§7): doctor, token usage, models.

Doctor wraps the existing read-only checks. Usage aggregates the
``token_usage`` table. The model registry serves built-ins from
:mod:`backend.config` plus user-added local models persisted in
``.argus/models.json`` — never the vault, never any API key (I4).
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import Settings, load_user_models, save_user_models
from backend.db import connect, init_schema
from backend.doctor import Check, run_checks
from backend.usage import Range, UsageReport, usage_report

MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class ModelInfo(BaseModel):
    """One registry entry. ``builtin`` models cannot be deleted."""

    name: str
    provider: str  # "anthropic" | "openai-compat"
    endpoint: str | None = None
    key_ref: str | None = None  # keyring reference only — never a secret (I4)
    default: bool = False
    builtin: bool = False


class AddModelRequest(BaseModel):
    """Register a local OpenAI-compatible model (e.g. ollama)."""

    name: str
    endpoint: str


def build_system_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.post("/doctor", response_model=list[Check])
    def doctor() -> list[Check]:
        """Run the existing health checks (read-only against the vault)."""
        return run_checks(settings)

    @router.get("/usage", response_model=UsageReport)
    def usage(range: Range = "session") -> UsageReport:  # noqa: A002 - API param name
        conn = db()
        try:
            return usage_report(conn, range)
        finally:
            conn.close()

    def _registry() -> list[ModelInfo]:
        return [
            ModelInfo(
                **entry,
                builtin=entry["provider"] == "anthropic" and entry.get("endpoint") is None,
            )
            for entry in settings.models
        ]

    @router.get("/models", response_model=list[ModelInfo])
    def list_models() -> list[ModelInfo]:
        return _registry()

    @router.post("/models", response_model=ModelInfo, status_code=201)
    def add_model(request: AddModelRequest) -> ModelInfo:
        name = request.name.strip()
        if not MODEL_NAME_RE.match(name):
            raise HTTPException(status_code=422, detail="invalid model name")
        endpoint = request.endpoint.strip()
        if not endpoint.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="endpoint must be an http(s) URL")
        if any(model.name == name for model in _registry()):
            raise HTTPException(status_code=409, detail=f"model {name} already exists")
        user_models = load_user_models(settings.models_file)
        user_models.append({"name": name, "provider": "openai-compat", "endpoint": endpoint})
        save_user_models(settings.models_file, user_models)
        return ModelInfo(name=name, provider="openai-compat", endpoint=endpoint)

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
        return {"status": "deleted", "name": name}

    return router
