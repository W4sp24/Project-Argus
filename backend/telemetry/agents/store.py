"""User-registered agent sources — ``.argus/agent-sources.json``.

Same shape as :mod:`backend.core.model_registry` and
:mod:`backend.features.integrations.store`: a plain JSON list beside the sqlite
db, never in the vault, tolerant of a corrupt or absent file. No secrets here —
a log folder is a path, not a credential.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: An id becomes a URL path segment and a ``cli_usage.agent`` value.
AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Ids the built-in sources own. A custom source may never claim one, or its
#: rows would merge into a built-in's totals and survive its deletion.
RESERVED_IDS = frozenset({"all", "claude-code", "claude-subagents", "codex"})


def slugify(name: str) -> str:
    """A display name reduced to a usable id (``Gemini CLI`` -> ``gemini-cli``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "agent"


def load_sources(path: Path) -> list[dict[str, Any]]:
    """Registered sources ([] when the file is absent, empty, or corrupt)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            entry
            for entry in payload
            if isinstance(entry, dict) and entry.get("id") and entry.get("path")
        ]
    except Exception:  # noqa: BLE001 - an unreadable registry is an empty one
        return []


def save_sources(path: Path, sources: list[dict[str, Any]]) -> None:
    """Persist beside the sqlite db (never the vault)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
