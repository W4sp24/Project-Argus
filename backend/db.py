"""SQLite storage for FRIDAY.

One small database in the vault's ``.friday/`` folder holds suggestion rows,
sync state, and (later phases) exams and audit entries. Plain ``sqlite3`` in
WAL mode — no ORM needed at this size.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    kind         TEXT NOT NULL CHECK (kind IN ('schedule', 'task', 'note')),
    payload_json TEXT NOT NULL,
    rationale    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'applied', 'dismissed')),
    applied_at   TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the FRIDAY database at ``db_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist yet. Safe to call repeatedly."""
    conn.executescript(SCHEMA)
    conn.commit()
