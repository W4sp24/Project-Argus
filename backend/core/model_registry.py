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

from pathlib import Path

from backend.core.jsonstore import load_json, save_json

DEFAULT_MODELS: tuple[dict, ...] = (
    {"name": "claude-sonnet-5", "provider": "anthropic", "default": True},
    {"name": "claude-haiku-4-5-20251001", "provider": "anthropic", "default": False},
)

# Static USD per **million** tokens, for the usage dashboard's cost estimate
# (redesign §14). Estimates only — real billing is the provider's business.
# Keyed on what actually lands in ``token_usage.model``, which is the
# *provider-side* id an adapter ran (``resolve_run_target``), not the display
# name someone gave the registry entry. A "my-deepseek" entry serving
# ``deepseek-chat`` is priced by the latter.
#
# Rates below are point-in-time estimates (checked 2026-08); providers change
# them, and a stale number here shows up as a wrong figure on the dashboard
# rather than a wrong bill. Anything absent prices at ``ZERO_RATE`` and is named
# as unpriced instead of guessed at — so leaving a model out is always safe.
MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},  # planner/generate (agent/*.py)
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    # Google, via backend/agent/gemini_api.py.
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    # DeepSeek and Groq, via the OpenAI-compatible adapter. Only the
    # tool-calling models are listed: deepseek-reasoner cannot call tools and
    # registration refuses it, so it can never appear in a usage row.
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
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
    """User-registered local models from ``models.json`` ([] when absent/corrupt).

    A corrupt file is quarantined rather than silently replaced — see
    :mod:`backend.core.jsonstore`.
    """
    payload = load_json(models_file, [])
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("name")]


def save_user_models(models_file: Path, models: list[dict]) -> None:
    """Persist user-registered models next to the sqlite db (never the vault)."""
    save_json(models_file, models)


def load_model_prefs(prefs_file: Path) -> dict:
    """Model preferences (currently just ``default``) — {} when absent/corrupt."""
    payload = load_json(prefs_file, {})
    return payload if isinstance(payload, dict) else {}


def save_model_prefs(prefs_file: Path, prefs: dict) -> None:
    """Persist model preferences beside ``models.json``.

    A separate file, deliberately: ``models.json`` is a bare JSON array whose
    shape predates this feature, and keeping the chosen default out of it means
    no migration and no risk to registries written by older versions.
    """
    save_json(prefs_file, prefs)
