"""SQLite storage for Argus.

One small database in the vault's ``.argus/`` folder holds suggestion rows,
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

CREATE TABLE IF NOT EXISTS exams (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    course         TEXT NOT NULL,
    title          TEXT NOT NULL,
    questions_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks_cache (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL,
    line      INTEGER NOT NULL,
    text      TEXT NOT NULL,
    done      INTEGER NOT NULL DEFAULT 0,
    due       TEXT,
    scheduled TEXT,
    priority  TEXT,
    tags      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    entry_point TEXT NOT NULL,
    model       TEXT NOT NULL,
    paths_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    exam_id      INTEGER NOT NULL REFERENCES exams(id),
    score        INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    answers_json TEXT NOT NULL,
    weak_topics  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS token_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    feature       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cli_usage_files (
    path       TEXT PRIMARY KEY,
    mtime_ns   INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cli_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT NOT NULL,
    ts            TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cli_usage_file_path ON cli_usage(file_path);

CREATE TABLE IF NOT EXISTS flashcard_decks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    course     TEXT NOT NULL,
    title      TEXT NOT NULL,
    cards_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flashcard_reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    card_id        TEXT NOT NULL,
    deck_id        INTEGER NOT NULL REFERENCES flashcard_decks(id),
    grade          TEXT NOT NULL CHECK (grade IN ('again', 'hard', 'good', 'easy')),
    state          INTEGER NOT NULL,
    step           INTEGER,
    stability      REAL NOT NULL,
    difficulty     REAL NOT NULL,
    due_at         TEXT NOT NULL,
    last_review_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_flashcard_reviews_card ON flashcard_reviews(deck_id, card_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the Argus database at ``db_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist yet. Safe to call repeatedly."""
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(suggestions)")}
    if "dismiss_reason" not in columns:  # lightweight migration for pre-P3 databases
        conn.execute("ALTER TABLE suggestions ADD COLUMN dismiss_reason TEXT")
    usage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(token_usage)")}
    if "cache_creation_input_tokens" not in usage_columns:  # migration for pre-cache-token DBs
        conn.execute("ALTER TABLE token_usage ADD COLUMN cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE token_usage ADD COLUMN cache_read_input_tokens INTEGER NOT NULL DEFAULT 0")
    conn.commit()
