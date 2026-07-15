"""Application settings loaded from a simple ``.env`` file.

Argus deliberately keeps configuration primitive: one ``KEY=VALUE`` file next
to the repo root (``VAULT_PATH``, ``BACKEND_PORT``). Secrets never live here —
they belong in the OS keyring (invariant I4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env")
DEFAULT_BACKEND_PORT = 8000

# --- Model registry (redesign §7) -------------------------------------------
# Built-in models use the existing agent auth (subscription login, I5) — no
# API keys here (I4). User-added local models are OpenAI-compatible endpoints
# persisted in ``.argus/models.json`` (the argus config/db dir, not the vault).

DEFAULT_MODELS: tuple[dict, ...] = (
    {"name": "claude-sonnet-4", "provider": "anthropic", "default": True},
    {"name": "claude-haiku", "provider": "anthropic", "default": False},
)

# Static USD per **million** tokens, for the usage dashboard's cost estimate
# (redesign §14). Estimates only — real billing is the provider's business.
MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-haiku": {"input": 0.80, "output": 4.0},
}
FALLBACK_RATE = MODEL_RATES["claude-opus-4-8"]  # today's agent model (runtime.py)


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


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def parse_env_file(env_file: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` env file, ignoring blanks and comments."""
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings for the backend."""

    backend_port: int = DEFAULT_BACKEND_PORT
    _vault_path: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """Load settings from ``env_file`` (default ``./.env``)."""
        values = parse_env_file(env_file or DEFAULT_ENV_FILE)
        vault_raw = values.get("VAULT_PATH")
        return cls(
            backend_port=int(values.get("BACKEND_PORT", DEFAULT_BACKEND_PORT)),
            _vault_path=Path(vault_raw) if vault_raw else None,
        )

    @property
    def vault_path(self) -> Path:
        """The Obsidian vault root. Accessing it unconfigured is an error."""
        if self._vault_path is None:
            raise ConfigError(
                "VAULT_PATH is not configured. Run `argus init <path>` or set it in .env."
            )
        return self._vault_path

    @property
    def db_path(self) -> Path:
        """SQLite database location — inside the vault's ``.argus/`` folder."""
        return self.vault_path / ".argus" / "argus.db"

    @property
    def models_file(self) -> Path:
        """Where user-registered local models persist (config dir, not vault notes)."""
        return self.db_path.parent / "models.json"

    @property
    def models(self) -> list[dict]:
        """Full model registry: built-ins first, then user-added local models."""
        registry = [dict(entry) for entry in DEFAULT_MODELS]
        known = {entry["name"] for entry in registry}
        for entry in load_user_models(self.models_file):
            if entry["name"] not in known:
                registry.append(
                    {
                        "name": str(entry["name"]),
                        "provider": str(entry.get("provider", "openai-compat")),
                        "endpoint": entry.get("endpoint"),
                        "key_ref": entry.get("key_ref"),
                        "default": False,
                    }
                )
        return registry
