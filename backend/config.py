"""Application settings loaded from a simple ``.env`` file.

Argus deliberately keeps configuration primitive: one ``KEY=VALUE`` file next
to the repo root (``VAULT_PATH``, ``BACKEND_PORT``). Secrets never live here —
they belong in the OS keyring (invariant I4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env")
DEFAULT_BACKEND_PORT = 8000


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
