"""The model registry: which models exist, where they live, what they cost.

Built-in models use the Claude Code CLI's subscription auth (I5) — no API keys
here (I4). User-added models are either OpenAI-compatible endpoints (local
Ollama or a hosted open-weight provider) or the Anthropic API, all persisted in
``.argus/models.json`` (the argus config/db dir, not the vault). Any API key
lives in the OS keyring and is referenced by ``key_ref`` only — see
:mod:`backend.agent.credentials`.

Split out of :mod:`backend.core.config` so that module is about *settings*:
``Settings`` composes the two, reading the files below to resolve its registry.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MODELS: tuple[dict, ...] = (
    {"name": "claude-sonnet-5", "provider": "anthropic", "default": True},
    {"name": "claude-haiku-4-5-20251001", "provider": "anthropic", "default": False},
)

# Static USD per **million** tokens, for the usage dashboard's cost estimate
# (redesign §14). Estimates only — real billing is the provider's business.
MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},  # planner/generate (agent/*.py)
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}
FALLBACK_RATE = MODEL_RATES["claude-opus-4-8"]  # today's agent model (runtime.py)

# Argus's own calls can now run on a free local model or on a hosted provider
# whose prices Argus does not know, so :mod:`backend.telemetry.usage` prices
# unknown models at zero and names them instead of guessing. Billing a local
# Llama at Opus rates would be actively misleading; "unpriced" is honest.
# ``FALLBACK_RATE`` still applies in :mod:`backend.telemetry.claude_cli`, where
# every model genuinely is an Anthropic one.
ZERO_RATE = {"input": 0.0, "output": 0.0}


def load_user_models(models_file: Path) -> list[dict]:
    """User-registered local models from ``models.json`` ([] when absent/corrupt)."""
    try:
        payload = json.loads(models_file.read_text(encoding="utf-8"))
        return [entry for entry in payload if isinstance(entry, dict) and entry.get("name")]
    except Exception:
        return []


def save_user_models(models_file: Path, models: list[dict]) -> None:
    """Persist user-registered models next to the sqlite db (never the vault)."""
    models_file.parent.mkdir(parents=True, exist_ok=True)
    models_file.write_text(json.dumps(models, ensure_ascii=False, indent=1), encoding="utf-8")


def load_model_prefs(prefs_file: Path) -> dict:
    """Model preferences (currently just ``default``) — {} when absent/corrupt."""
    try:
        payload = json.loads(prefs_file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_model_prefs(prefs_file: Path, prefs: dict) -> None:
    """Persist model preferences beside ``models.json``.

    A separate file, deliberately: ``models.json`` is a bare JSON array whose
    shape predates this feature, and keeping the chosen default out of it means
    no migration and no risk to registries written by older versions.
    """
    prefs_file.parent.mkdir(parents=True, exist_ok=True)
    prefs_file.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
