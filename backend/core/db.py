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

-- Keyed by (path, agent), not path alone: two sources legitimately read the
-- same file. Claude Code's foreground and subagent sources share one projects
-- directory, and a user-registered source may point anywhere. With `path` as
-- the sole key each scan would evict the other's high-water mark and re-ingest
-- everything forever.
CREATE TABLE IF NOT EXISTS cli_usage_files (
    path       TEXT NOT NULL,
    agent      TEXT NOT NULL DEFAULT 'claude-code',
    mtime_ns   INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (path, agent)
);

CREATE TABLE IF NOT EXISTS cli_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path     TEXT NOT NULL,
    agent         TEXT NOT NULL DEFAULT 'claude-code',
    ts            TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cli_usage_file_path ON cli_usage(file_path);
-- The (agent, ts) index cannot live here: on a database created before the
-- agent column existed, CREATE TABLE IF NOT EXISTS is a no-op and this script
-- runs before the migration below, so indexing a column that is not there yet
-- fails the whole open. It is created in init_schema after the ALTER.

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

CREATE TABLE IF NOT EXISTS quick_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    label      TEXT NOT NULL,
    url        TEXT NOT NULL,
    icon       TEXT,
    icon_kind  TEXT,
    icon_value TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_quick_links_sort_order ON quick_links(sort_order);

CREATE TABLE IF NOT EXISTS automation_widgets (
    slug                       TEXT PRIMARY KEY,
    title                      TEXT,
    kind                       TEXT NOT NULL
                               CHECK (kind IN
                                   ('metric', 'list', 'table', 'timeline', 'text', 'chart')),
    payload                    TEXT NOT NULL,
    last_seen_at               TEXT,
    expected_interval_seconds  INTEGER,
    created_at                 TEXT NOT NULL,
    position                   INTEGER,
    pinned                     INTEGER NOT NULL DEFAULT 0,
    hidden                     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id            TEXT PRIMARY KEY,
    workflow_id   TEXT NOT NULL,
    workflow_name TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL
                  CHECK (status IN ('running', 'ok', 'failed', 'timeout', 'unresolved')),
    mode          TEXT CHECK (mode IS NULL OR mode IN ('ack', 'status', 'widget')),
    message       TEXT,
    execution_id  TEXT,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_automation_runs_started_at ON automation_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS automation_prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the Argus database at ``db_path``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 30s, not the sqlite3 default of 5. WAL lets readers and one writer run
    # together, but writers still serialise, and this database has a writer the
    # user never sees: the boot-time auto-index/watch thread (backend/main.py)
    # rewrites state whenever the vault changes. Five seconds was short enough
    # that a request landing during that work got `sqlite3.OperationalError:
    # database is locked` instead of waiting its turn -- surfacing as a 500
    # from /api/agenda and an empty task list on the dashboard, which is what
    # made the e2e task specs flaky.
    conn = sqlite3.connect(db_path, timeout=30.0)
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
        conn.execute(
            "ALTER TABLE token_usage ADD COLUMN "
            "cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "ALTER TABLE token_usage ADD COLUMN cache_read_input_tokens INTEGER NOT NULL DEFAULT 0"
        )
    ql_columns = {row["name"] for row in conn.execute("PRAGMA table_info(quick_links)")}
    if "icon_kind" not in ql_columns:  # migration for pre-custom-icon DBs (glyph-only)
        conn.execute("ALTER TABLE quick_links ADD COLUMN icon_kind TEXT")
        conn.execute("ALTER TABLE quick_links ADD COLUMN icon_value TEXT")
    # Multi-agent usage: one table now serves Claude Code, Codex, and whatever
    # comes next. Everything recorded before this column existed came from
    # Claude Code, which is exactly what the default backfills.
    cli_columns = {row["name"] for row in conn.execute("PRAGMA table_info(cli_usage)")}
    if "agent" not in cli_columns:
        conn.execute("ALTER TABLE cli_usage ADD COLUMN agent TEXT NOT NULL DEFAULT 'claude-code'")
    scan_columns = {row["name"] for row in conn.execute("PRAGMA table_info(cli_usage_files)")}
    if "agent" not in scan_columns:
        conn.execute(
            "ALTER TABLE cli_usage_files ADD COLUMN agent TEXT NOT NULL DEFAULT 'claude-code'"
        )
    # Safe only now that the column is guaranteed to exist — see SCHEMA.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cli_usage_agent ON cli_usage(agent, ts)")
    _migrate_scan_key(conn)
    conn.commit()


def _migrate_scan_key(conn: sqlite3.Connection) -> None:
    """Re-key ``cli_usage_files`` from ``path`` to ``(path, agent)``.

    SQLite cannot alter a primary key, so this is the documented rebuild:
    create, copy, drop, rename. Guarded on the *old* shape, so it runs at most
    once and is a no-op on a database created from the current SCHEMA.

    Rows carry over as-is. Everything scanned under the single-key schema
    belonged to one agent, so no row can collide with another on the way in.
    """
    keys = [row for row in conn.execute("PRAGMA table_info(cli_usage_files)") if row["pk"]]
    if {row["name"] for row in keys} == {"path", "agent"}:
        return  # already re-keyed

    conn.executescript(
        """
        CREATE TABLE cli_usage_files_new (
            path       TEXT NOT NULL,
            agent      TEXT NOT NULL DEFAULT 'claude-code',
            mtime_ns   INTEGER NOT NULL,
            size       INTEGER NOT NULL,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (path, agent)
        );
        INSERT OR REPLACE INTO cli_usage_files_new (path, agent, mtime_ns, size, scanned_at)
            SELECT path, agent, mtime_ns, size, scanned_at FROM cli_usage_files;
        DROP TABLE cli_usage_files;
        ALTER TABLE cli_usage_files_new RENAME TO cli_usage_files;
        """
    )
