"""The external MCP server registry — ``.argus/mcp-servers.json``.

Mirrors :mod:`backend.core.model_registry` deliberately: a plain JSON list next
to the sqlite db (never in the vault), tolerant of a corrupt or absent file,
and holding **no secrets**. A bearer token for an HTTP server lives in the OS
keyring under a ``key_ref``, exactly like a model's API key (invariant I4).

This is the registry Argus stores; making these servers' tools callable from
in-app chat is separate work in :mod:`backend.agent.adapters`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.core.jsonstore import load_json, save_json

#: Same shape as a model name — it becomes a keyring reference and a URL path.
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

TRANSPORTS = ("stdio", "http")


def key_ref_for(name: str) -> str:
    """Keyring reference for an MCP server's bearer token."""
    return f"mcp:{name}"


def load_servers(path: Path) -> list[dict[str, Any]]:
    """Registered servers ([] when the file is absent, empty, or corrupt).

    Corrupt is quarantined, not overwritten — see :mod:`backend.core.jsonstore`.
    """
    payload = load_json(path, [])
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("name")]


def save_servers(path: Path, servers: list[dict[str, Any]]) -> None:
    """Persist the registry beside the sqlite db (never the vault)."""
    save_json(path, servers)
