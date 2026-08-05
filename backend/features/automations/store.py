"""The n8n automations data layer.

Two halves live in this module, mirroring the split already used elsewhere in
the codebase:

**The instance registry** (``.argus/automations.json``) mirrors
:mod:`backend.features.integrations.store` deliberately: a plain JSON
document beside the sqlite db (never in the vault), tolerant of a corrupt or
absent file, holding **no secrets**. Argus registers exactly one n8n
instance, so this is a single entry (or ``None``), not a list. The instance's
API key lives in the OS keyring under ``key_ref_for(name)``, exactly like a
model's API key or an MCP server's bearer token (invariant I4) — never in
this file.

**The sqlite half** (``automation_widgets``, ``automation_runs``,
``automation_prefs``) is plain ``sqlite3``, plain-dict CRUD, matching
:mod:`backend.vault.suggestions` / :mod:`backend.features.quick_links.store`.
Every function that reads the clock takes ``now`` as a keyword-only
parameter defaulting to :func:`_utcnow`, so tests can inject a fixed clock —
there is no ``freezegun``/``time-machine`` dependency in this repo.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.jsonstore import load_json, save_json

# Same shape as a model/MCP-server name — becomes a keyring reference.
INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def key_ref_for(name: str) -> str:
    """Keyring reference for the n8n instance's API key."""
    return f"n8n:{name}"


def load_instance(path: Path) -> dict[str, Any] | None:
    """The single registered n8n instance, or ``None`` when there isn't one.

    Corrupt is quarantined, not overwritten — see :mod:`backend.core.jsonstore`.
    """
    payload = load_json(path, {})
    if not isinstance(payload, dict) or not payload.get("name"):
        return None
    return payload


def save_instance(path: Path, entry: dict[str, Any]) -> None:
    """Persist the single registered instance beside the sqlite db (never the vault)."""
    save_json(path, entry)


def delete_instance(path: Path) -> None:
    """Clear the registered instance. The caller is responsible for the keyring entry."""
    save_json(path, {})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    """ISO-8601 UTC string for a (possibly naive) datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    """Parse a stored ISO-8601 timestamp back into a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# --- widgets ----------------------------------------------------------------


def _widget_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "title": row["title"],
        "kind": row["kind"],
        "payload": json.loads(row["payload"]),
        "last_seen_at": row["last_seen_at"],
        "expected_interval_seconds": row["expected_interval_seconds"],
        "created_at": row["created_at"],
        "position": row["position"],
        "pinned": bool(row["pinned"]),
        "hidden": bool(row["hidden"]),
    }


def next_position(conn: sqlite3.Connection) -> int:
    """The next auto-placement position, appended after the current highest."""
    row = conn.execute("SELECT MAX(position) AS max_position FROM automation_widgets").fetchone()
    max_position = row["max_position"]
    return 0 if max_position is None else int(max_position) + 1


def upsert_widget(
    conn: sqlite3.Connection,
    slug: str,
    kind: str,
    payload: Any,
    *,
    title: str | None = None,
    expected_interval_seconds: int | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Insert or update the widget at ``slug``.

    On update, ``created_at``, ``position``, ``pinned`` and ``hidden`` are left
    untouched — only the content columns and ``last_seen_at`` change.

    ``title`` and ``expected_interval_seconds`` are *retained* when the caller
    omits them rather than being nulled. A workflow that declares a cadence on
    one push and omits it on the next would otherwise silently lose staleness
    detection, and a widget that cannot go stale is exactly the silently-empty
    panel this feature exists to prevent.
    """
    ts = _isoformat(now())
    payload_json = json.dumps(payload, ensure_ascii=False)
    existing = conn.execute(
        "SELECT 1 FROM automation_widgets WHERE slug = ?", (slug,)
    ).fetchone()

    if existing is None:
        position = next_position(conn)
        conn.execute(
            "INSERT INTO automation_widgets "
            "(slug, title, kind, payload, last_seen_at, expected_interval_seconds, "
            "created_at, position, pinned, hidden) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (slug, title, kind, payload_json, ts, expected_interval_seconds, ts, position),
        )
    else:
        conn.execute(
            "UPDATE automation_widgets SET title = COALESCE(?, title), kind = ?, payload = ?, "
            "last_seen_at = ?, "
            "expected_interval_seconds = COALESCE(?, expected_interval_seconds) "
            "WHERE slug = ?",
            (title, kind, payload_json, ts, expected_interval_seconds, slug),
        )
    conn.commit()
    widget = get_widget(conn, slug)
    assert widget is not None  # just written above
    return widget


def get_widget(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """The widget at ``slug``, or ``None`` if it doesn't exist."""
    row = conn.execute("SELECT * FROM automation_widgets WHERE slug = ?", (slug,)).fetchone()
    return None if row is None else _widget_row_to_dict(row)


def list_widgets(conn: sqlite3.Connection, *, include_hidden: bool = False) -> list[dict[str, Any]]:
    """All widgets, ordered by ``position`` then ``created_at``."""
    query = "SELECT * FROM automation_widgets"
    if not include_hidden:
        query += " WHERE hidden = 0"
    query += " ORDER BY position, created_at"
    rows = conn.execute(query).fetchall()
    return [_widget_row_to_dict(row) for row in rows]


def set_widget_flags(
    conn: sqlite3.Connection,
    slug: str,
    *,
    pinned: bool | None = None,
    hidden: bool | None = None,
    position: int | None = None,
) -> None:
    """Update only the provided flags/position; omitted ones are left as-is."""
    fields: list[str] = []
    values: list[Any] = []
    if pinned is not None:
        fields.append("pinned = ?")
        values.append(1 if pinned else 0)
    if hidden is not None:
        fields.append("hidden = ?")
        values.append(1 if hidden else 0)
    if position is not None:
        fields.append("position = ?")
        values.append(position)
    if not fields:
        return
    values.append(slug)
    conn.execute(f"UPDATE automation_widgets SET {', '.join(fields)} WHERE slug = ?", values)
    conn.commit()


def delete_widget(conn: sqlite3.Connection, slug: str) -> None:
    """Delete the widget at ``slug``. A no-op if it doesn't exist."""
    conn.execute("DELETE FROM automation_widgets WHERE slug = ?", (slug,))
    conn.commit()


# The keys checked for "genuinely zero items" — first one present on the
# payload wins. A ``metric`` payload legitimately has none of these and is
# never considered empty (see widget_state).
_EMPTY_LIST_KEYS = ("items", "rows", "entries", "series")


def _payload_is_empty(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in _EMPTY_LIST_KEYS:
        if key in payload:
            value = payload[key]
            return isinstance(value, list) and len(value) == 0
    if "body" in payload:
        return payload["body"] in ("", None)
    return False


def widget_state(row: dict[str, Any], *, now: Callable[[], datetime] = _utcnow) -> str:
    """One of ``"live" | "stale" | "empty" | "waiting"`` for a widget row.

    - ``"waiting"``: no payload has ever arrived (``last_seen_at`` is ``None``).
    - ``"stale"``: fresher than never, but ``now - last_seen_at`` exceeds
      2.5x the declared cadence. A widget with no declared cadence
      (``expected_interval_seconds`` is ``None``) can never go stale.
    - ``"empty"``: a fresh payload arrived but carries zero items (an empty
      ``items``/``rows``/``entries``/``series`` list, or an empty ``body``).
      A ``metric`` kind is never ``"empty"``.
    - ``"live"``: otherwise.
    """
    last_seen_at = row.get("last_seen_at")
    if not last_seen_at:
        return "waiting"

    expected = row.get("expected_interval_seconds")
    if expected is not None:
        elapsed = (now() - _parse_iso(last_seen_at)).total_seconds()
        if elapsed > 2.5 * expected:
            return "stale"

    if row.get("kind") != "metric" and _payload_is_empty(row.get("payload")):
        return "empty"

    return "live"


# --- runs ---------------------------------------------------------------


def _run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "workflow_id": row["workflow_id"],
        "workflow_name": row["workflow_name"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "mode": row["mode"],
        "message": row["message"],
        "execution_id": row["execution_id"],
        "payload": None if row["payload"] is None else json.loads(row["payload"]),
    }


def record_run_started(
    conn: sqlite3.Connection,
    run_id: str,
    workflow_id: str,
    *,
    workflow_name: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Record a new run as ``running``."""
    conn.execute(
        "INSERT INTO automation_runs (id, workflow_id, workflow_name, started_at, status) "
        "VALUES (?, ?, ?, ?, 'running')",
        (run_id, workflow_id, workflow_name, _isoformat(now())),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    *,
    mode: str | None = None,
    message: str | None = None,
    execution_id: str | None = None,
    payload: Any = None,
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Close out a run: sets ``finished_at`` and the terminal ``status``."""
    payload_json = None if payload is None else json.dumps(payload, ensure_ascii=False)
    conn.execute(
        "UPDATE automation_runs SET status = ?, finished_at = ?, mode = ?, message = ?, "
        "execution_id = ?, payload = ? WHERE id = ?",
        (status, _isoformat(now()), mode, message, execution_id, payload_json, run_id),
    )
    conn.commit()


def list_runs(
    conn: sqlite3.Connection, *, limit: int = 50, workflow_id: str | None = None
) -> list[dict[str, Any]]:
    """Runs newest-first, optionally filtered to one workflow."""
    if workflow_id is None:
        rows = conn.execute(
            "SELECT * FROM automation_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM automation_runs WHERE workflow_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (workflow_id, limit),
        ).fetchall()
    return [_run_row_to_dict(row) for row in rows]


def recent_runs_for(
    conn: sqlite3.Connection, workflow_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    """The most recent runs for one workflow — e.g. "last 5 runs, 2 failed"."""
    return list_runs(conn, limit=limit, workflow_id=workflow_id)


def expire_stale_runs(
    conn: sqlite3.Connection, *, ttl_seconds: int, now: Callable[[], datetime] = _utcnow
) -> int:
    """Mark still-``running`` rows older than ``ttl_seconds`` as ``unresolved``.

    The async-never-arrived path: a workflow that started but whose n8n
    callback never showed up. Returns how many rows were expired.
    """
    cutoff = _isoformat(now() - timedelta(seconds=ttl_seconds))
    cursor = conn.execute(
        "UPDATE automation_runs SET status = 'unresolved' "
        "WHERE status = 'running' AND started_at < ?",
        (cutoff,),
    )
    conn.commit()
    return cursor.rowcount


# --- prefs ----------------------------------------------------------------


def get_pref(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """The stored value for ``key``, or ``default`` when unset."""
    row = conn.execute("SELECT value FROM automation_prefs WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def set_pref(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set (or replace) the stored value for ``key``."""
    conn.execute(
        "INSERT INTO automation_prefs (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
