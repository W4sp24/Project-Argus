"""The n8n automations data layer.

Two halves live in this module, mirroring the split already used elsewhere in
the codebase:

**The instance registry** (``.argus/automations.json``) mirrors
:mod:`backend.features.integrations.store` deliberately: a plain JSON *list*
document beside the sqlite db (never in the vault), tolerant of a corrupt or
absent file, holding **no secrets**. Argus used to register exactly one n8n
instance; :func:`load_instances`/:func:`save_instances` are the list-shaped
API multi-instance support needs — see their docstrings for the legacy
single-dict self-heal. Each instance's API key lives in the OS keyring under
``key_ref_for(name)``, exactly like a model's API key or an MCP server's
bearer token (invariant I4) — never in this file.

**The sqlite half** (``automation_widgets``, ``automation_runs``,
``automation_workflows``, ``automation_events``, ``automation_prefs``) is
plain ``sqlite3``, plain-dict CRUD, matching :mod:`backend.vault.suggestions`
/ :mod:`backend.features.quick_links.store`. Every function that reads the
clock takes ``now`` as a keyword-only parameter defaulting to
:func:`_utcnow`, so tests can inject a fixed clock — there is no
``freezegun``/``time-machine`` dependency in this repo.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.jsonstore import load_json, save_json

# Same shape as a model/MCP-server name — becomes a keyring reference.
INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def key_ref_for(name: str) -> str:
    """Keyring reference for an n8n instance's API key."""
    return f"n8n:{name}"


def load_instances(path: Path) -> list[dict[str, Any]]:
    """Registered n8n instances (``[]`` when the file is absent, empty, or corrupt).

    Corrupt is quarantined, not overwritten — see :mod:`backend.core.jsonstore`.

    A file written before multi-instance support held a single dict (or
    ``{}`` for "none registered") rather than a list. That legacy shape is
    self-healed here on first read: the dict is upgraded in place — given an
    ``id`` (a fresh uuid4 hex) and a ``kind`` (default ``"LOCAL"``) if it
    doesn't already have them — wrapped in a one-element list, written back
    to disk immediately, and returned.

    ``key_ref_for(name)`` is unchanged by this upgrade, and the entry's
    existing ``key_ref`` is carried over untouched (``dict(payload)`` copies
    every field, including it, before the ``id``/``kind`` defaults are
    applied). There is no delete-then-write window on the credential and no
    orphaned keyring secret: the OS keyring entry the legacy row already
    pointed at keeps working without anything else changing.
    """
    payload = load_json(path, [])
    if isinstance(payload, dict):
        if not payload.get("name"):
            return []  # {} (or any dict without a name) means "none registered"
        entry = dict(payload)
        entry.setdefault("id", uuid.uuid4().hex)
        entry.setdefault("kind", "LOCAL")
        instances = [entry]
        save_instances(path, instances)
        return instances
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("name")]


def save_instances(path: Path, instances: list[dict[str, Any]]) -> None:
    """Persist the registry beside the sqlite db (never the vault)."""
    save_json(path, instances)


# --- B1 compatibility wrappers ----------------------------------------------
#
# router.py is unchanged in this chunk and still calls the single-instance
# API below. B2 removes these three functions once the router is migrated to
# load_instances/save_instances directly.


def load_instance(path: Path) -> dict[str, Any] | None:
    """The first registered n8n instance, or ``None`` when there isn't one."""
    instances = load_instances(path)
    return instances[0] if instances else None


def save_instance(path: Path, entry: dict[str, Any]) -> None:
    """Persist ``entry`` as the sole registered instance.

    Callers only ever reach this when no instance is registered yet (see
    ``register_instance``'s 409 guard in the router), so replacing the whole
    list with a single entry matches the old single-dict overwrite exactly.
    """
    save_instances(path, [entry])


def delete_instance(path: Path) -> None:
    """Clear the registered instance. The caller is responsible for the keyring entry."""
    save_instances(path, [])


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
        "instance_id": row["instance_id"],
        "title": row["title"],
        "kind": row["kind"],
        "payload": json.loads(row["payload"]),
        "last_seen_at": row["last_seen_at"],
        "expected_interval_seconds": row["expected_interval_seconds"],
        "created_at": row["created_at"],
        "position": row["position"],
        "pinned": bool(row["pinned"]),
        "hidden": bool(row["hidden"]),
        "grid_cols": row["grid_cols"],
        "grid_rows": row["grid_rows"],
        "layout_locked": bool(row["layout_locked"]),
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
    instance_id: str = "",
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Insert or update the widget at ``(instance_id, slug)``.

    ``instance_id`` defaults to ``""`` — the "not yet attributed to an
    instance" sentinel (see ``backend.core.db.SCHEMA``) — not ``None``.
    ``instance_id`` is part of the primary key and the column is
    ``NOT NULL``; SQLite never treats two NULLs as equal inside a PRIMARY
    KEY, so a ``None``/NULL value here would silently opt this row out of
    the uniqueness the key is supposed to guarantee.

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
        "SELECT 1 FROM automation_widgets WHERE slug = ? AND instance_id = ?",
        (slug, instance_id),
    ).fetchone()

    if existing is None:
        position = next_position(conn)
        conn.execute(
            "INSERT INTO automation_widgets "
            "(slug, instance_id, title, kind, payload, last_seen_at, expected_interval_seconds, "
            "created_at, position, pinned, hidden) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (
                slug,
                instance_id,
                title,
                kind,
                payload_json,
                ts,
                expected_interval_seconds,
                ts,
                position,
            ),
        )
    else:
        conn.execute(
            "UPDATE automation_widgets SET title = COALESCE(?, title), kind = ?, payload = ?, "
            "last_seen_at = ?, "
            "expected_interval_seconds = COALESCE(?, expected_interval_seconds) "
            "WHERE slug = ? AND instance_id = ?",
            (title, kind, payload_json, ts, expected_interval_seconds, slug, instance_id),
        )
    conn.commit()
    widget = get_widget(conn, slug, instance_id=instance_id)
    assert widget is not None  # just written above
    return widget


def get_widget(
    conn: sqlite3.Connection, slug: str, *, instance_id: str = ""
) -> dict[str, Any] | None:
    """The widget at ``(instance_id, slug)``, or ``None`` if it doesn't exist.

    ``instance_id`` defaults to ``""``, the "not yet attributed" sentinel —
    see :func:`upsert_widget`.
    """
    row = conn.execute(
        "SELECT * FROM automation_widgets WHERE slug = ? AND instance_id = ?",
        (slug, instance_id),
    ).fetchone()
    return None if row is None else _widget_row_to_dict(row)


def list_widgets(
    conn: sqlite3.Connection,
    *,
    include_hidden: bool = False,
    instance_id: str | None = None,
) -> list[dict[str, Any]]:
    """Widgets, ordered by ``position`` then ``created_at``.

    ``instance_id`` unset (the default, ``None``) lists across every
    instance, exactly matching the pre-multi-instance behaviour when every
    row's instance_id is ``""`` (the "not yet attributed" sentinel — see
    :func:`upsert_widget`). Pass ``""`` explicitly to list only unattributed
    widgets, or a real instance id to scope to one instance.
    """
    query = "SELECT * FROM automation_widgets WHERE 1=1"
    params: list[Any] = []
    if not include_hidden:
        query += " AND hidden = 0"
    if instance_id is not None:
        query += " AND instance_id = ?"
        params.append(instance_id)
    query += " ORDER BY position, created_at"
    rows = conn.execute(query, params).fetchall()
    return [_widget_row_to_dict(row) for row in rows]


def set_widget_flags(
    conn: sqlite3.Connection,
    slug: str,
    *,
    pinned: bool | None = None,
    hidden: bool | None = None,
    position: int | None = None,
    grid_cols: int | None = None,
    grid_rows: int | None = None,
    layout_locked: bool | None = None,
    instance_id: str = "",
) -> None:
    """Update only the provided flags/position/layout; omitted ones are left as-is.

    ``grid_cols``/``grid_rows`` are range-checked here (1..4) because the
    fresh-DB CHECK constraint (see ``backend.core.db.SCHEMA``) cannot be
    retrofitted onto a database that reached this shape via ``ALTER TABLE``
    — SQLite has no "add a CHECK to an existing column" operation.
    """
    if grid_cols is not None and not (1 <= grid_cols <= 4):
        raise ValueError(f"grid_cols must be between 1 and 4, got {grid_cols}")
    if grid_rows is not None and not (1 <= grid_rows <= 4):
        raise ValueError(f"grid_rows must be between 1 and 4, got {grid_rows}")

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
    if grid_cols is not None:
        fields.append("grid_cols = ?")
        values.append(grid_cols)
    if grid_rows is not None:
        fields.append("grid_rows = ?")
        values.append(grid_rows)
    if layout_locked is not None:
        fields.append("layout_locked = ?")
        values.append(1 if layout_locked else 0)
    if not fields:
        return
    values.extend([slug, instance_id])
    conn.execute(
        f"UPDATE automation_widgets SET {', '.join(fields)} WHERE slug = ? AND instance_id = ?",
        values,
    )
    conn.commit()


def delete_widget(conn: sqlite3.Connection, slug: str, *, instance_id: str = "") -> None:
    """Delete the widget at ``(instance_id, slug)``. A no-op if it doesn't exist."""
    conn.execute(
        "DELETE FROM automation_widgets WHERE slug = ? AND instance_id = ?", (slug, instance_id)
    )
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
        "instance_id": row["instance_id"],
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
    instance_id: str = "",
    now: Callable[[], datetime] = _utcnow,
) -> None:
    """Record a new run as ``running``.

    ``instance_id`` defaults to ``""`` (not ``None``): the column is
    ``NOT NULL``, defaulted to the same "not yet attributed" sentinel used
    across ``automation_widgets``/``automation_workflows``/
    ``automation_events``, even though it is not part of a key here.
    """
    conn.execute(
        "INSERT INTO automation_runs "
        "(id, workflow_id, workflow_name, instance_id, started_at, status) "
        "VALUES (?, ?, ?, ?, ?, 'running')",
        (run_id, workflow_id, workflow_name, instance_id, _isoformat(now())),
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


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    """One run by id, or ``None``.

    Used by the external surface to confirm a ``?run={id}`` push belongs to a
    run that actually exists before it is recorded against one.
    """
    row = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else _run_row_to_dict(row)


def list_runs(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    workflow_id: str | None = None,
    instance_id: str | None = None,
) -> list[dict[str, Any]]:
    """Runs newest-first, optionally filtered to one workflow and/or instance."""
    query = "SELECT * FROM automation_runs WHERE 1=1"
    params: list[Any] = []
    if workflow_id is not None:
        query += " AND workflow_id = ?"
        params.append(workflow_id)
    if instance_id is not None:
        query += " AND instance_id = ?"
        params.append(instance_id)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
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


# --- workflow cache ---------------------------------------------------------
#
# Added for the router chunk (backend.features.automations.router): the last
# known shape of every argus-tagged n8n workflow, so the dashboard can render
# cards from cache — marked DISCONNECTED, RUN disabled — when n8n itself is
# unreachable. Same plain-dict-CRUD style as the widgets/runs above; nothing
# here changes their behaviour.


def _workflow_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "instance_id": row["instance_id"],
        "name": row["name"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "schema_json": json.loads(row["schema_json"]) if row["schema_json"] else None,
        "active": bool(row["active"]),
        "last_seen_at": row["last_seen_at"],
    }


def upsert_workflow(
    conn: sqlite3.Connection,
    workflow_id: str,
    *,
    name: str | None,
    tags: list[str],
    schema_json: dict[str, Any] | None,
    active: bool,
    instance_id: str = "",
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Insert or refresh the cached row for ``workflow_id`` on ``instance_id``.

    Keyed on ``(instance_id, workflow_id)``, not ``workflow_id`` alone —
    ``workflow_id`` is n8n's own id, a small per-instance value on
    self-hosted installs, so two different instances can hand out the same
    id to unrelated workflows. Before this fix the table's sole key was
    ``id`` and this function used ``ON CONFLICT(id) DO UPDATE``, so
    refreshing instance B's cache could silently overwrite instance A's
    cached row for a same-numbered but unrelated workflow. See
    ``backend.core.db._migrate_workflow_key`` for the matching schema
    rebuild.

    ``instance_id`` defaults to ``""`` — the "not yet attributed to an
    instance" sentinel — not ``None``. ``automation_workflows.instance_id``
    is ``NOT NULL``: SQLite never treats two NULLs as equal inside a
    PRIMARY KEY, so ``ON CONFLICT(instance_id, id)`` would silently never
    fire for two rows that both carried NULL, which is exactly the
    uniqueness hole this rebuild exists to close. With the NOT NULL
    sentinel in place, ``ON CONFLICT(instance_id, id) DO UPDATE`` fires
    correctly again, so this uses it rather than an explicit exists-then-
    branch.

    Always a full overwrite (no COALESCE-and-retain like ``upsert_widget``):
    every field here comes from the same discovery response n8n just gave us,
    so there is nothing stale worth preserving across the write.
    """
    ts = _isoformat(now())
    conn.execute(
        "INSERT INTO automation_workflows "
        "(id, instance_id, name, tags, schema_json, active, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(instance_id, id) DO UPDATE SET name = excluded.name, "
        "tags = excluded.tags, schema_json = excluded.schema_json, "
        "active = excluded.active, last_seen_at = excluded.last_seen_at",
        (
            workflow_id,
            instance_id,
            name,
            json.dumps(list(tags), ensure_ascii=False),
            None if schema_json is None else json.dumps(schema_json, ensure_ascii=False),
            1 if active else 0,
            ts,
        ),
    )
    conn.commit()
    workflow = get_workflow(conn, workflow_id, instance_id=instance_id)
    assert workflow is not None  # just written above
    return workflow


def get_workflow(
    conn: sqlite3.Connection, workflow_id: str, *, instance_id: str = ""
) -> dict[str, Any] | None:
    """The cached row for ``(instance_id, workflow_id)``, or ``None`` if it isn't cached.

    ``instance_id`` defaults to ``""``, the "not yet attributed" sentinel —
    see :func:`upsert_workflow`.
    """
    row = conn.execute(
        "SELECT * FROM automation_workflows WHERE id = ? AND instance_id = ?",
        (workflow_id, instance_id),
    ).fetchone()
    return None if row is None else _workflow_row_to_dict(row)


def list_workflows(
    conn: sqlite3.Connection, *, instance_id: str | None = None
) -> list[dict[str, Any]]:
    """Cached workflows, ordered by name (nulls last) then id.

    ``instance_id`` unset (the default, ``None``) lists across every
    instance, exactly matching the pre-multi-instance behaviour when every
    row's instance_id is ``""`` (the "not yet attributed" sentinel — see
    :func:`upsert_workflow`). Pass ``""`` explicitly to list only
    unattributed workflows, or a real instance id to scope to one instance.
    """
    query = "SELECT * FROM automation_workflows WHERE 1=1"
    params: list[Any] = []
    if instance_id is not None:
        query += " AND instance_id = ?"
        params.append(instance_id)
    query += " ORDER BY name IS NULL, name, id"
    rows = conn.execute(query, params).fetchall()
    return [_workflow_row_to_dict(row) for row in rows]


def delete_missing_workflows(
    conn: sqlite3.Connection, seen_ids: list[str], *, instance_id: str = ""
) -> int:
    """Drop every cached workflow for ``instance_id`` whose id is not in ``seen_ids``.

    Called after a discovery pass with the ids n8n just returned for
    ``?tags=argus`` — anything cached but not seen this time either lost the
    tag or was deleted in n8n, and its card must disappear. An empty
    ``seen_ids`` (nothing tagged ``argus`` anymore, or the instance was just
    unregistered) clears the whole cache for ``instance_id``. Returns the
    number of rows dropped.

    ``instance_id`` defaults to ``""``, the "not yet attributed" sentinel —
    see :func:`upsert_workflow`.
    """
    if not seen_ids:
        cursor = conn.execute(
            "DELETE FROM automation_workflows WHERE instance_id = ?", (instance_id,)
        )
    else:
        placeholders = ", ".join("?" for _ in seen_ids)
        cursor = conn.execute(
            "DELETE FROM automation_workflows "
            f"WHERE instance_id = ? AND id NOT IN ({placeholders})",
            [instance_id, *seen_ids],
        )
    conn.commit()
    return cursor.rowcount


# --- events ------------------------------------------------------------
#
# The ambient activity log behind the dashboard's status line — one row per
# notable thing that happened (a run, a push, a failure, an install, a
# capture). Independent of automation_runs/automation_widgets: an event can
# reference either (or neither) via ``subject``, but this table's only job is
# a short, bounded, newest-first feed.

#: Kept in sync with the CHECK constraint on automation_events.tag.
EVENT_TAGS = ("RUN", "PUSH", "FAIL", "INSTALL", "CAPTURE")

#: Enforced in application code (see record_event), not a sqlite trigger —
#: same style as expire_stale_runs.
EVENT_RETENTION_CAP = 1000


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ts": row["ts"],
        "instance_id": row["instance_id"],
        "tag": row["tag"],
        "subject": row["subject"],
        "text": row["text"],
    }


def record_event(
    conn: sqlite3.Connection,
    *,
    tag: str,
    text: str,
    instance_id: str = "",
    subject: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Append one event, then prune anything past the newest ``EVENT_RETENTION_CAP`` rows.

    ``instance_id`` defaults to ``""`` (not ``None``): the column is
    ``NOT NULL``, defaulted to the same "not yet attributed" sentinel used
    across ``automation_widgets``/``automation_workflows``/
    ``automation_runs``, even though it is not part of a key here.

    Retention lives here in application code rather than a sqlite trigger,
    matching ``expire_stale_runs``'s style elsewhere in this module: after
    every insert, all but the newest ``EVENT_RETENTION_CAP`` rows (by id) are
    deleted, so the table cannot grow without bound.
    """
    ts = _isoformat(now())
    cursor = conn.execute(
        "INSERT INTO automation_events (ts, instance_id, tag, subject, text) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, instance_id, tag, subject, text),
    )
    event_id = cursor.lastrowid
    conn.execute(
        "DELETE FROM automation_events WHERE id NOT IN "
        "(SELECT id FROM automation_events ORDER BY id DESC LIMIT ?)",
        (EVENT_RETENTION_CAP,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM automation_events WHERE id = ?", (event_id,)
    ).fetchone()
    assert row is not None  # just written above, and the cap is well above 0
    return _event_row_to_dict(row)


def list_events(
    conn: sqlite3.Connection,
    *,
    tag: str | None = None,
    instance_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Events newest-first, optionally filtered by tag and/or instance."""
    query = "SELECT * FROM automation_events WHERE 1=1"
    params: list[Any] = []
    if tag is not None:
        query += " AND tag = ?"
        params.append(tag)
    if instance_id is not None:
        query += " AND instance_id = ?"
        params.append(instance_id)
    query += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_event_row_to_dict(row) for row in rows]


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
