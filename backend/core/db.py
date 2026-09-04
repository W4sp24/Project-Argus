"""SQLite storage for Argus.

One small database in the vault's ``.argus/`` folder holds suggestion rows,
sync state, and (later phases) exams and audit entries. Plain ``sqlite3`` in
WAL mode — no ORM needed at this size.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("argus.db")

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
    tags      TEXT NOT NULL DEFAULT '',
    -- The 🔁 rule, verbatim. Cached rather than re-parsed because every task
    -- the UI renders comes out of this table, not out of parse_task_line --
    -- so without it a recurring task is indistinguishable from a one-off
    -- everywhere the user can actually see one.
    recurrence TEXT
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
    -- Vestigial. Cards live in flashcard_cards below; this is written '[]' and
    -- read by nothing. Dropping it means a create/copy/drop/rename rebuild of
    -- a table flashcard_reviews holds a foreign key into -- real risk for a
    -- cosmetic gain -- so it stays, saying "no cards here", which is true.
    cards_json TEXT NOT NULL
);

-- Cards are rows, not a JSON blob, because they are now authored: created by
-- hand, edited, reordered, starred, suspended and imported one at a time.
--
-- `card_ref` is the key flashcard_reviews.card_id joins on. Migrated cards
-- keep the "{deck_id}:{index}" string the blob-era generator produced, which
-- is precisely what lets _migrate_flashcard_cards leave every review row
-- untouched. New cards get "c<uuid4 hex>", which cannot collide with that
-- shape.
CREATE TABLE IF NOT EXISTS flashcard_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id     INTEGER NOT NULL REFERENCES flashcard_decks(id),
    card_ref    TEXT    NOT NULL,
    front       TEXT    NOT NULL,
    back        TEXT    NOT NULL,
    hint        TEXT,
    position    INTEGER NOT NULL,
    starred     INTEGER NOT NULL DEFAULT 0,
    suspended   INTEGER NOT NULL DEFAULT 0,
    -- The vault note an imported card came from, so a deck can say where it
    -- got its material. NULL for hand-written cards.
    source_path TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_flashcard_cards_ref
    ON flashcard_cards(deck_id, card_ref);
CREATE INDEX IF NOT EXISTS idx_flashcard_cards_deck
    ON flashcard_cards(deck_id, position);

-- Match is a game, so its scores are activity, not scheduling state: they live
-- nowhere near flashcard_reviews and nothing reads them into FSRS.
CREATE TABLE IF NOT EXISTS flashcard_match_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id    INTEGER NOT NULL REFERENCES flashcard_decks(id),
    elapsed_ms INTEGER NOT NULL,
    pairs      INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_flashcard_match_deck ON flashcard_match_scores(deck_id, elapsed_ms);

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

-- One user action's worth of long-running work: N files ingested, a vault
-- reindex, a study guide or practice exam generated. The table keeps the
-- `ingest_` name it was created under rather than being renamed -- a rename
-- costs a rebuild and buys nothing the `kind` column does not already say.
--
-- This exists because that work had no record at all: `POST /api/ingest`
-- embedded on the request thread and returned a chunk count,
-- `/api/study/upload` returned "indexing in background" and nothing ever said
-- whether it finished, `/api/study/guide` awaited a multi-minute generation
-- inside the request, and reindex status was a single in-process object with
-- three fields. None of them could say what was at which stage, so slow or
-- partial work was indistinguishable from broken work.
--
-- `boot_id` is what makes a restart recoverable: rows carrying a different
-- boot id than the running process belong to a job whose thread no longer
-- exists, so they are reconciled to 'failed' once at startup rather than
-- polling forever. `automation_runs` has the same problem and solves it with
-- an 'unresolved' status; the in-memory reindex `_State` this table replaced
-- sidestepped it for free by resetting on restart, which a table does not get
-- -- so boot_id reconciliation is what buys that property back.
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id             TEXT PRIMARY KEY,
    boot_id        TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT,
    status         TEXT NOT NULL
                   CHECK (status IN ('queued', 'running', 'ok', 'partial', 'failed')),
    -- Which kind of long-running work this row records. Deliberately NOT in a
    -- CHECK constraint: SQLite cannot alter one, so every new kind would cost
    -- a create/copy/drop/rename rebuild of a table that is already the durable
    -- record of work in flight. The vocabulary lives in
    -- `ingest.store.JOB_KINDS` instead, where adding to it is a one-line
    -- change. 'ingest' is the default because every row written before this
    -- column existed was one.
    kind           TEXT NOT NULL DEFAULT 'ingest',
    -- The job's own inputs and results as JSON, for the kinds whose facts have
    -- no column of their own: a guide's `scope`, an exam's `n`/`difficulty`
    -- and the `exam_id` it minted, a path-scoped reindex's `paths`. A column
    -- per kind would be a column that is NULL for every other kind, and the
    -- set of kinds is expected to grow.
    params         TEXT,
    target         TEXT NOT NULL,
    summary_prompt TEXT NOT NULL DEFAULT '',
    -- Which of `ingest.notes.NOTE_STYLES` shaped the generated note, or ''
    -- for "no note". Kept beside `summary_prompt` rather than replacing it:
    -- a style and a free-text instruction compose, they do not exclude.
    note_style     TEXT NOT NULL DEFAULT '',
    total          INTEGER NOT NULL DEFAULT 0,
    done           INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_created_at
    ON ingest_jobs(created_at DESC, id DESC);

-- One row per file, and the reason the UI can show a real pipeline instead of
-- a spinner. `stage` is the wire shape the progress list renders directly.
-- 'skipped' is a first-class outcome, not a failure: a `#no-ai` file whose
-- summary was deliberately not generated must say so, because silently not
-- summarising looks exactly like a bug.
-- `ON DELETE CASCADE` relies on connect()'s PRAGMA foreign_keys=ON.
CREATE TABLE IF NOT EXISTS ingest_job_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES ingest_jobs(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    path         TEXT,
    stage        TEXT NOT NULL
                 CHECK (stage IN ('queued', 'saving', 'indexing', 'summarizing',
                                  'done', 'failed', 'skipped')),
    chunks       INTEGER NOT NULL DEFAULT 0,
    summary_path TEXT,
    error        TEXT,
    -- Which stage the item stopped at, when it stopped early. `stage` alone
    -- cannot say: it collapses to 'failed' (or to 'done' when only the note
    -- broke), so a file that saved and indexed fine and lost only its note
    -- was indistinguishable from one that was never written at all -- and the
    -- progress readout painted every segment red for both. NULL means the
    -- item ran to its end with nothing withheld.
    failed_stage TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_job_items_job
    ON ingest_job_items(job_id, id);

-- The native calendar: one row per calendar the user has, whether it is the
-- built-in local one ('local', created by ensure_default_calendar so a fresh
-- install can make an event with zero setup) or a subscribed .ics feed
-- ('ics'). A secret iCal URL *is* a credential -- anyone holding it can read
-- the calendar -- so it lives in the OS keyring under `url_ref` (invariant
-- I4) and never in this table; `url_display` holds only a redaction fit to
-- show in the UI.
--
-- Two deliberate choices, both of which cost a table rebuild to reverse:
--
-- `calendar_events.calendar_id` is TEXT NOT NULL because it is half of that
-- table's PRIMARY KEY, and SQLite permits NULLs inside a PRIMARY KEY and
-- never treats two of them as equal -- so a nullable key column enforces
-- nothing at all. That is the `automation_widgets` lesson above, applied
-- before the rows exist rather than after.
--
-- `kind` carries no CHECK constraint on purpose. SQLite cannot alter one, so
-- every kind a later feature adds (CalDAV, an imported .ics snapshot) would
-- cost a create/copy/drop/rename rebuild of the table holding the user's
-- calendars. The vocabulary lives in `calendar.store.CALENDAR_KINDS`
-- instead, where extending it is a one-line change -- same reasoning as
-- `ingest_jobs.kind`.
CREATE TABLE IF NOT EXISTS calendars (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    kind                     TEXT NOT NULL DEFAULT 'local',
    color                    TEXT NOT NULL DEFAULT '',
    url_ref                  TEXT,
    url_display              TEXT,
    refresh_interval_seconds INTEGER NOT NULL DEFAULT 3600,
    last_sync_at             TEXT,
    last_sync_error          TEXT,
    etag                     TEXT,
    enabled                  INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT NOT NULL
);

-- Keyed on (calendar_id, id) rather than a surrogate: an .ics feed's UID is
-- unique only within the feed that issued it, and `store.replace_events`
-- rewrites one calendar's rows wholesale on every sync, so the calendar is
-- part of an event's identity. `start`/`end` are ISO-8601 strings, which sort
-- lexicographically in the same order they sort chronologically -- that is
-- what lets the half-open window query use the index below directly and lets
-- one column hold an all-day date beside a timed instant.
-- `rrule`/`exdates` store the recurrence rule unexpanded; expansion happens
-- at read time, bounded by the query window.
CREATE TABLE IF NOT EXISTS calendar_events (
    calendar_id TEXT NOT NULL,
    id          TEXT NOT NULL,
    title       TEXT NOT NULL,
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    all_day     INTEGER NOT NULL DEFAULT 0,
    location    TEXT,
    notes       TEXT,
    rrule       TEXT,
    exdates     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (calendar_id, id)
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start);
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
    # tasks_cache is rebuilt from markdown on every read, so this column needs
    # no backfill -- but it does need the ALTER. CREATE TABLE IF NOT EXISTS is
    # a no-op on an existing DB, so without this every refresh_cache INSERT
    # would fail on an install that predates the column, i.e. all of them.
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks_cache)")}
    if "recurrence" not in task_columns:
        conn.execute("ALTER TABLE tasks_cache ADD COLUMN recurrence TEXT")
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
    ingest_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ingest_jobs)")}
    if "note_style" not in ingest_columns:  # migration for pre-note-style DBs
        conn.execute("ALTER TABLE ingest_jobs ADD COLUMN note_style TEXT NOT NULL DEFAULT ''")
    if "kind" not in ingest_columns:  # migration for pre-generalised-job DBs
        # Everything recorded before the job store served more than ingestion
        # was an ingest, which is exactly what the default backfills.
        conn.execute("ALTER TABLE ingest_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'ingest'")
    if "params" not in ingest_columns:
        conn.execute("ALTER TABLE ingest_jobs ADD COLUMN params TEXT")
    item_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ingest_job_items)")}
    if "failed_stage" not in item_columns:  # migration for pre-failed-stage DBs
        conn.execute("ALTER TABLE ingest_job_items ADD COLUMN failed_stage TEXT")

    # Decks became authorable: they can be renamed, described, and can belong
    # to no course at all (course = '', since relaxing a NOT NULL in SQLite
    # would cost a table rebuild to buy nothing a value cannot say).
    deck_columns = {row["name"] for row in conn.execute("PRAGMA table_info(flashcard_decks)")}
    if "description" not in deck_columns:
        conn.execute("ALTER TABLE flashcard_decks ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if "source" not in deck_columns:
        # Everything that existed before decks could be authored was generated,
        # which is exactly what the default backfills.
        conn.execute(
            "ALTER TABLE flashcard_decks ADD COLUMN source TEXT NOT NULL DEFAULT 'generated'"
        )
    if "updated_at" not in deck_columns:
        # No DEFAULT (datetime('now')): SQLite rejects a non-constant default in
        # ALTER TABLE ADD COLUMN, so it is backfilled below instead.
        conn.execute("ALTER TABLE flashcard_decks ADD COLUMN updated_at TEXT")
    if "source_paths" not in deck_columns:
        # Which files a generated deck was written from, as a JSON array of
        # vault-relative paths -- so a deck can say "from lecture-04.pdf" and the
        # SOURCES rail can badge the file a deck came out of.
        #
        # No backfill line, unlike updated_at directly above, and the asymmetry
        # is the point: '[]' is a *constant* default, so the ALTER fills every
        # existing row and every future INSERT that omits the column. An empty
        # list for a deck nobody generated is the truth, not a gap.
        conn.execute(
            "ALTER TABLE flashcard_decks ADD COLUMN source_paths TEXT NOT NULL DEFAULT '[]'"
        )
    # Unconditional, not folded into the branch above: the column has no
    # default, so *any* INSERT that omits it leaves a NULL -- not just the rows
    # that predate the column. Running it every open makes the table
    # self-healing instead of correct exactly once.
    conn.execute("UPDATE flashcard_decks SET updated_at = created_at WHERE updated_at IS NULL")
    _migrate_flashcard_cards(conn)

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


def _migrate_flashcard_cards(conn: sqlite3.Connection) -> None:
    """Explode every legacy ``cards_json`` blob into ``flashcard_cards`` rows.

    Additive by construction. Each row's ``card_ref`` is the very
    ``"{deck_id}:{index}"`` string the blob-era generator wrote into
    ``cards_json`` and that ``flashcard_reviews.card_id`` already holds, so
    **no review row is read, rewritten, or backfilled** and every card keeps
    the FSRS state it earned. That is the whole reason for keying cards by a
    string rather than by their new row id.

    Idempotent: a deck that already has rows is skipped, so this runs on every
    ``init_schema`` call for the life of the database.
    """
    migrated = {
        row["deck_id"] for row in conn.execute("SELECT DISTINCT deck_id FROM flashcard_cards")
    }
    for row in conn.execute("SELECT id, cards_json FROM flashcard_decks").fetchall():
        deck_id = row["id"]
        if deck_id in migrated:
            continue
        try:
            cards = json.loads(row["cards_json"])
        except (TypeError, ValueError):
            # A hand-edited or truncated blob is not worth failing every
            # database open over -- and this runs on every open. The deck
            # arrives empty and can be re-imported.
            logger.warning("flashcard deck %s has unreadable cards_json; leaving it empty", deck_id)
            continue
        if not isinstance(cards, list) or not cards:
            continue
        rows = [
            (
                deck_id,
                card.get("id") or f"{deck_id}:{index}",
                card["front"],
                card["back"],
                index,
            )
            for index, card in enumerate(cards)
            # A card missing either face is not a card. The blob-era parser
            # dropped these too; this keeps that contract.
            if isinstance(card, dict) and card.get("front") and card.get("back")
        ]
        if rows:
            conn.executemany(
                "INSERT INTO flashcard_cards (deck_id, card_ref, front, back, position)"
                " VALUES (?, ?, ?, ?, ?)",
                rows,
            )
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
