"""Audit trail of every agent prompt: WHICH vault files were sent, never text.

Rows record entry point, model, and a path list only (I3 — content stays
local; the audit answers "what did FRIDAY read?" without copying it).
Logging is best-effort by design: a broken audit must never break chat.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: int
    created_at: str
    entry_point: str
    model: str
    paths: list[str]


def log_prompt(db_path: Path, entry_point: str, model: str, paths: list[str]) -> None:
    """Record one prompt's vault-path list. Swallows all errors."""
    try:
        from backend.db import connect, init_schema

        conn = connect(db_path)
        try:
            init_schema(conn)
            conn.execute(
                "INSERT INTO audit (entry_point, model, paths_json) VALUES (?, ?, ?)",
                (entry_point, model, json.dumps(sorted(set(paths)), ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: S110 - audit is strictly best-effort
        pass


def log_prompt_conn(
    conn: sqlite3.Connection, entry_point: str, model: str, paths: list[str]
) -> None:
    """Same as :func:`log_prompt` on an existing connection. Swallows errors."""
    try:
        conn.execute(
            "INSERT INTO audit (entry_point, model, paths_json) VALUES (?, ?, ?)",
            (entry_point, model, json.dumps(sorted(set(paths)), ensure_ascii=False)),
        )
        conn.commit()
    except Exception:  # noqa: S110 - audit is strictly best-effort
        pass


def recent(conn: sqlite3.Connection, limit: int = 100) -> list[AuditEntry]:
    rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [
        AuditEntry(
            id=row["id"],
            created_at=row["created_at"],
            entry_point=row["entry_point"],
            model=row["model"],
            paths=json.loads(row["paths_json"]),
        )
        for row in rows
    ]
