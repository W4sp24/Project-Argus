"""Application settings loaded from a simple ``.env`` file.

Argus deliberately keeps configuration primitive: one ``KEY=VALUE`` file next
to the repo root (``VAULT_PATH``, ``BACKEND_PORT``). Secrets never live here —
they belong in the OS keyring (invariant I4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.model_registry import DEFAULT_MODELS, load_model_prefs, load_user_models

# Repo-relative by default (dev, `argus web`, tests). The packaged desktop app
# has no repo root, so the Electron shell points this at its own userData dir
# via ARGUS_ENV_FILE — that one override reaches Settings.load, argus doctor,
# reindex, watch, and init_vault's _write_env without changing any signature.
DEFAULT_ENV_FILE = Path(os.environ.get("ARGUS_ENV_FILE", ".env"))
DEFAULT_BACKEND_PORT = 8000


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def parse_env_file(env_file: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` env file, ignoring blanks and comments."""
    values: dict[str, str] = {}
    if not env_file.is_file():
        return values
    # utf-8-sig, not utf-8: Notepad and PowerShell's `Out-File -Encoding utf8`
    # write a BOM, and Python's str.strip() does not remove ﻿ -- so the
    # first key would silently parse as "﻿VAULT_PATH" and Argus would
    # report VAULT_PATH as unconfigured. Identical to utf-8 when no BOM.
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
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
    def model_prefs_file(self) -> Path:
        """Where the chosen default model persists, beside ``models.json``."""
        return self.db_path.parent / "model-prefs.json"

    @property
    def mcp_servers_file(self) -> Path:
        """Where registered external MCP servers persist, beside ``models.json``."""
        return self.db_path.parent / "mcp-servers.json"

    @property
    def gcal_credentials_file(self) -> Path:
        """Where the Google OAuth *client* file lands when uploaded in-app.

        Not a secret token — the client JSON identifies the app, and the token
        it yields goes to the keyring (I4). It lives in the config dir rather
        than the repo root so a packaged desktop install has somewhere real to
        put it; see :func:`backend.connectors.gcal.connect`.
        """
        return self.db_path.parent / "gcal-credentials.json"

    def _registry(self) -> list[dict]:
        """Built-ins first, then user-added models, before the default is applied."""
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
                        # The provider-side id, when it differs from the display
                        # name (a "groq-llama" entry serving "llama-3.3-70b").
                        "model_id": entry.get("model_id"),
                        "default": False,
                    }
                )
        return registry

    def _resolve_default(self, registry: list[dict]) -> str:
        """The effective default: the user's pick, else the built-in flag.

        A stale pick (a model since deleted) falls back rather than dangling —
        otherwise removing your default would silently break every feature that
        runs without an explicit model.
        """
        chosen = load_model_prefs(self.model_prefs_file).get("default")
        names = {entry["name"] for entry in registry}
        if isinstance(chosen, str) and chosen in names:
            return chosen
        for entry in DEFAULT_MODELS:
            if entry.get("default") and entry["name"] in names:
                return str(entry["name"])
        return str(registry[0]["name"]) if registry else str(DEFAULT_MODELS[0]["name"])

    @property
    def models(self) -> list[dict]:
        """Full model registry: built-ins first, then user-added models.

        Exactly one entry carries ``default: True`` — the user's choice when
        they have made one, otherwise the built-in default.
        """
        registry = self._registry()
        default = self._resolve_default(registry)
        for entry in registry:
            entry["default"] = entry["name"] == default
        return registry

    @property
    def default_model(self) -> str:
        """Name of the model used when a caller does not name one."""
        return self._resolve_default(self._registry())
