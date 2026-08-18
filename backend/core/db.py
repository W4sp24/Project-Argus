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

-- `instance_id` is NOT NULL, defaulted to ''  -- deliberately not nullable.
-- SQLite permits NULL inside a PRIMARY KEY and never treats two NULLs as
-- equal for uniqueness purposes, so a nullable instance_id here would mean
-- this primary key enforces nothing at all: every row written before B3
-- backfills real instance ids carries the same value, and if that value
-- were NULL, duplicate slugs could pile up with no constraint noticing.
-- '' is the explicit "not yet attributed to an instance" sentinel a later
-- chunk backfills. Keyed on (instance_id, slug), not slug alone -- the
-- approved multi-instance design is explicit that the same widget slug on
-- two n8n instances is two automations, not one.
CREATE TABLE IF NOT EXISTS automation_widgets (
    slug                       TEXT NOT NULL,
    instance_id                TEXT NOT NULL DEFAULT '',
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
    hidden                     INTEGER NOT NULL DEFAULT 0,
    grid_cols                  INTEGER NOT NULL DEFAULT 1 CHECK (grid_cols BETWEEN 1 AND 4),
    grid_rows                  INTEGER NOT NULL DEFAULT 1 CHECK (grid_rows BETWEEN 1 AND 4),
    layout_locked              INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, slug)
);

-- instance_id is not part of a key here (run ids are already globally
-- unique), but it is still NOT NULL DEFAULT '' rather than nullable -- one
-- representation of "not yet attributed to an instance" across the whole
-- feature (automation_widgets, automation_workflows, automation_events),
-- not two.
CREATE TABLE IF NOT EXISTS automation_runs (
    id            TEXT PRIMARY KEY,
    workflow_id   TEXT NOT NULL,
    workflow_name TEXT,
    instance_id   TEXT NOT NULL DEFAULT '',
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

-- The workflow cache: the last known shape of every argus-tagged n8n
-- workflow, refreshed by POST /automations/refresh. When n8n is unreachable
-- the dashboard still renders cards from this table (marked DISCONNECTED,
-- RUN disabled) instead of going blank because a second service is down.
-- `schema_json` holds the *raw* workflow definition n8n returned (not a
-- pre-parsed schema): re-running schema.parse_workflow on it at read time is
-- cheap and keeps the cache from drifting out of sync with that module's own
-- parsing rules as they evolve.
--
-- Keyed on (instance_id, id), not `id` alone. `id` is n8n's own workflow id,
-- a small per-instance value on self-hosted installs, so two different n8n
-- instances can (and, before this table was re-keyed, did) hand out the same
-- id to unrelated workflows -- see `_migrate_workflow_key` below.
-- instance_id is NOT NULL DEFAULT '' for the same reason as
-- automation_widgets above: SQLite never treats two NULLs as equal inside a
-- PRIMARY KEY, so a nullable instance_id here would mean this key enforces
-- no uniqueness at all until B3 backfills real ids. '' is the "not yet
-- attributed to an instance" sentinel.
CREATE TABLE IF NOT EXISTS automation_workflows (
    id           TEXT NOT NULL,
    instance_id  TEXT NOT NULL DEFAULT '',
    name         TEXT,
    tags         TEXT,
    schema_json  TEXT,
    active       INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, id)
);

-- The ambient activity log behind the dashboard's status line: one row per
-- notable thing that happened (a run, a push, a failure, an install, a
-- capture), independent of the longer-lived `automation_runs`/
-- `automation_widgets` records those events often relate to. Retention is
-- enforced in application code (see store.record_event), not here.
-- instance_id is not part of a key here either, but NOT NULL DEFAULT '' for
-- the same consistency reason as automation_runs above.
CREATE TABLE IF NOT EXISTS automation_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    instance_id TEXT NOT NULL DEFAULT '',
    tag         TEXT NOT NULL CHECK (tag IN ('RUN', 'PUSH', 'FAIL', 'INSTALL', 'CAPTURE')),
    subject     TEXT,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_events_ts ON automation_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_automation_events_instance
    ON automation_events(instance_id, ts DESC);

-- `title` is derived from the first user message (see store.derive_title)
-- rather than asked for up front -- a thread the user never bothered to name
-- still has to be findable in the sidebar.
--
-- `course` is the Course Hub scope (NULL = global) and is fixed at creation:
-- a thread that started scoped to one course stays scoped, because its whole
-- transcript was retrieved under that filter, and re-scoping it later would
-- make earlier turns answer for a filter they were never run against.
--
-- `session_id`/`session_provider`/`session_model` are the claude-agent-sdk
-- resume token and the exact backend it belongs to. All three must match the
-- adapter about to run, or the token is ignored and history is replayed into
-- the prompt instead: a session id minted by the Claude CLI means nothing to
-- an Ollama model, and resuming it would silently answer from the wrong
-- transcript. NULL until the first run completes.
CREATE TABLE IF NOT EXISTS chat_threads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    course           TEXT,
    session_id       TEXT,
    session_provider TEXT,
    session_model    TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    archived         INTEGER NOT NULL DEFAULT 0
);
-- archived-first because the sidebar's only query is "newest unarchived".
-- id DESC is a tiebreaker column, not part of the filter: store.list_threads
-- orders ``updated_at DESC, id DESC`` so two threads touched in the same
-- second still sort deterministically.
CREATE INDEX IF NOT EXISTS idx_chat_threads_updated_at
    ON chat_threads(archived, updated_at DESC, id DESC);

-- `tools_json` holds the tool trace for an assistant turn as the WIRE shape
-- it was streamed in, not a re-derivable form: the trace records what
-- actually happened, and re-running a summarizer over it later would
-- describe a vault that has since changed. NULL on user rows.
-- `ON DELETE CASCADE` relies on connect()'s PRAGMA foreign_keys=ON.
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT NOT NULL,
    model      TEXT,
    tools_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
    ON chat_messages(thread_id, id);
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

    # Multi-instance n8n support (chunk B1): additive columns for databases
    # that reached automation_widgets/automation_runs/automation_workflows
    # before instance_id existed. The two PRIMARY KEY rebuilds that also need
    # these columns present run afterwards, via _migrate_widget_key and
    # _migrate_workflow_key.
    widget_columns = {row["name"] for row in conn.execute("PRAGMA table_info(automation_widgets)")}
    if "instance_id" not in widget_columns:
        conn.execute(
            "ALTER TABLE automation_widgets ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''"
        )
    if "grid_cols" not in widget_columns:
        conn.execute(
            "ALTER TABLE automation_widgets ADD COLUMN grid_cols INTEGER NOT NULL DEFAULT 1"
        )
        conn.execute(
            "ALTER TABLE automation_widgets ADD COLUMN grid_rows INTEGER NOT NULL DEFAULT 1"
        )
        conn.execute(
            "ALTER TABLE automation_widgets ADD COLUMN layout_locked INTEGER NOT NULL DEFAULT 0"
        )
    run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(automation_runs)")}
    if "instance_id" not in run_columns:
        conn.execute(
            "ALTER TABLE automation_runs ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''"
        )
    workflow_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(automation_workflows)")
    }
    if "instance_id" not in workflow_columns:
        conn.execute(
            "ALTER TABLE automation_workflows ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''"
        )
    _migrate_widget_key(conn)
    _migrate_workflow_key(conn)

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


def _migrate_widget_key(conn: sqlite3.Connection) -> None:
    """Re-key ``automation_widgets`` from ``slug`` to ``(instance_id, slug)``.

    Design decision, not a bug fix (contrast with ``_migrate_workflow_key``
    below): the approved multi-instance plan is explicit that the same
    widget slug on two n8n instances is two automations, not one, so the
    primary key must include ``instance_id``.

    SQLite cannot alter a primary key, so this is the documented rebuild:
    create, copy, drop, rename. Guarded on the *old* shape, so it runs at
    most once and is a no-op on a database created from the current SCHEMA.
    Runs after the ``instance_id``/``grid_cols``/``grid_rows``/
    ``layout_locked`` ALTER guards in ``init_schema``, so every column the
    copy references is guaranteed to already exist.

    Rows that predate this migration are copied through
    ``COALESCE(instance_id, '')``: the ALTER guard in ``init_schema`` adds
    ``instance_id`` as ``NOT NULL DEFAULT ''`` when it is missing, so in
    practice every existing row already has ``''`` by the time this runs —
    the ``COALESCE`` is defensive insurance against a stray NULL, not the
    normal path. ``''`` is the "not yet attributed to an instance" sentinel;
    a later chunk backfills the real id. A plain ``INSERT`` (not
    ``INSERT OR REPLACE``) is deliberate: the old table was keyed on
    ``slug`` alone, so two source rows colliding on ``(instance_id, slug)``
    should be impossible, and a silent ``OR REPLACE`` would be exactly the
    wrong way to find out that assumption broke.
    """
    keys = [row for row in conn.execute("PRAGMA table_info(automation_widgets)") if row["pk"]]
    if {row["name"] for row in keys} == {"instance_id", "slug"}:
        return  # already re-keyed

    conn.executescript(
        """
        CREATE TABLE automation_widgets_new (
            slug                       TEXT NOT NULL,
            instance_id                TEXT NOT NULL DEFAULT '',
            title                      TEXT,
            kind                       TEXT NOT NULL
                                       CHECK (kind IN
                                           ('metric', 'list', 'table', 'timeline', 'text',
                                            'chart')),
            payload                    TEXT NOT NULL,
            last_seen_at               TEXT,
            expected_interval_seconds  INTEGER,
            created_at                 TEXT NOT NULL,
            position                   INTEGER,
            pinned                     INTEGER NOT NULL DEFAULT 0,
            hidden                     INTEGER NOT NULL DEFAULT 0,
            grid_cols                  INTEGER NOT NULL DEFAULT 1 CHECK (grid_cols BETWEEN 1 AND 4),
            grid_rows                  INTEGER NOT NULL DEFAULT 1 CHECK (grid_rows BETWEEN 1 AND 4),
            layout_locked              INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (instance_id, slug)
        );
        INSERT INTO automation_widgets_new
            (slug, instance_id, title, kind, payload, last_seen_at,
             expected_interval_seconds, created_at, position, pinned, hidden,
             grid_cols, grid_rows, layout_locked)
            SELECT slug, COALESCE(instance_id, ''), title, kind, payload, last_seen_at,
                   expected_interval_seconds, created_at, position, pinned, hidden,
                   grid_cols, grid_rows, layout_locked
            FROM automation_widgets;
        DROP TABLE automation_widgets;
        ALTER TABLE automation_widgets_new RENAME TO automation_widgets;
        """
    )


def _migrate_workflow_key(conn: sqlite3.Connection) -> None:
    """Re-key ``automation_workflows`` from ``id`` to ``(instance_id, id)``.

    This is a correctness fix, not tidying. ``id`` is n8n's own workflow id,
    which is a small per-instance value on self-hosted installs, so two
    different n8n instances can hand out the same id to unrelated workflows.
    Under the old single-column primary key, ``store.upsert_workflow``'s
    ``ON CONFLICT(id) DO UPDATE`` treated those as the *same* row: refreshing
    instance B's workflow cache could silently overwrite instance A's cached
    row for a same-numbered but unrelated workflow. Re-keying on
    ``(instance_id, id)`` gives every instance its own id namespace.
    ``instance_id`` is ``NOT NULL DEFAULT ''`` rather than nullable — SQLite
    never treats two NULLs as equal inside a PRIMARY KEY, so a nullable
    column would have made this new key enforce nothing either, right up
    until B3 backfills real ids. With the sentinel in place,
    ``store.upsert_workflow``'s ``ON CONFLICT(instance_id, id) DO UPDATE``
    fires correctly again.

    SQLite cannot alter a primary key, so this is the documented rebuild:
    create, copy, drop, rename. Guarded on the *old* shape, so it runs at
    most once and is a no-op on a database created from the current SCHEMA.
    Runs after the ``instance_id`` ALTER guard in ``init_schema``, so the
    column the copy references is guaranteed to already exist.

    Rows that predate this migration are copied through
    ``COALESCE(instance_id, '')`` — defensive insurance against a stray
    NULL, since the ALTER guard already backfills ``''`` for every existing
    row. A plain ``INSERT`` (not ``INSERT OR REPLACE``) is deliberate: the
    old table's sole key was ``id`` (already unique), so no two rows can
    collide on the way in, and a silent ``OR REPLACE`` would be exactly the
    wrong way to find out that assumption broke.
    """
    keys = [row for row in conn.execute("PRAGMA table_info(automation_workflows)") if row["pk"]]
    if {row["name"] for row in keys} == {"instance_id", "id"}:
        return  # already re-keyed

    conn.executescript(
        """
        CREATE TABLE automation_workflows_new (
            id           TEXT NOT NULL,
            instance_id  TEXT NOT NULL DEFAULT '',
            name         TEXT,
            tags         TEXT,
            schema_json  TEXT,
            active       INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (instance_id, id)
        );
        INSERT INTO automation_workflows_new
            (id, instance_id, name, tags, schema_json, active, last_seen_at)
            SELECT id, COALESCE(instance_id, ''), name, tags, schema_json, active, last_seen_at
            FROM automation_workflows;
        DROP TABLE automation_workflows;
        ALTER TABLE automation_workflows_new RENAME TO automation_workflows;
        """
    )
