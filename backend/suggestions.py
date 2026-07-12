"""The suggestion queue: everything the agent wants to change, awaiting a click.

Rows are inserted by propose_* tools (and syllabus import) and only ever
executed by ``backend.writer.apply_suggestion`` after approval (I1).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel

Kind = Literal["schedule", "task", "note"]


class Suggestion(BaseModel):
    """One pending/applied/dismissed change proposal."""

    id: int
    created_at: str
    kind: Kind
    payload: dict[str, Any]
    rationale: str
    status: str
    applied_at: str | None = None
    dismiss_reason: str | None = None


def _row_to_suggestion(row: sqlite3.Row) -> Suggestion:
    return Suggestion(
        id=row["id"],
        created_at=row["created_at"],
        kind=row["kind"],
        payload=json.loads(row["payload_json"]),
        rationale=row["rationale"],
        status=row["status"],
        applied_at=row["applied_at"],
        dismiss_reason=row["dismiss_reason"],
    )


def insert_suggestion(
    conn: sqlite3.Connection, kind: Kind, payload: dict[str, Any], rationale: str
) -> int:
    cursor = conn.execute(
        "INSERT INTO suggestions (kind, payload_json, rationale) VALUES (?, ?, ?)",
        (kind, json.dumps(payload, ensure_ascii=False), rationale),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get(conn: sqlite3.Connection, suggestion_id: int) -> Suggestion | None:
    row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    return None if row is None else _row_to_suggestion(row)


def pending(conn: sqlite3.Connection) -> list[Suggestion]:
    rows = conn.execute(
        "SELECT * FROM suggestions WHERE status = 'pending' ORDER BY id DESC"
    ).fetchall()
    return [_row_to_suggestion(row) for row in rows]


def dismiss(conn: sqlite3.Connection, suggestion_id: int, reason: str) -> None:
    """Dismiss with a reason — fed back to the planner as preference signal."""
    conn.execute(
        "UPDATE suggestions SET status = 'dismissed', dismiss_reason = ? WHERE id = ?",
        (reason, suggestion_id),
    )
    conn.commit()


def mark_applied(conn: sqlite3.Connection, suggestion_id: int) -> None:
    conn.execute(
        "UPDATE suggestions SET status = 'applied', applied_at = datetime('now') WHERE id = ?",
        (suggestion_id,),
    )
    conn.commit()


def dismissal_feedback(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    """Recent dismissal reasons — planner context so it stops repeating itself."""
    rows = conn.execute(
        "SELECT rationale, dismiss_reason FROM suggestions"
        " WHERE status = 'dismissed' AND dismiss_reason IS NOT NULL"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [f"Dismissed “{row['rationale'][:80]}” because: {row['dismiss_reason']}" for row in rows]
