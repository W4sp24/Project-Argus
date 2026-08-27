"""The ingest job store — the record an ingest leaves behind.

Every function here takes a connection rather than opening one, matching
backend/features/automations/store.py. That is not stylistic: sqlite3
connections default to ``check_same_thread=True``, and the ingest pipeline is
the first background thread in Argus that writes to the database, so a
connection must be opened by whichever thread uses it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.ingest import store


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    """CREATE TABLE IF NOT EXISTS means no migration guard is needed."""
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    init_schema(connection)
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()

    assert {"ingest_jobs", "ingest_job_items"} <= tables


def test_a_database_created_before_these_tables_gains_them(tmp_path: Path) -> None:
    """The chat_threads precedent: additive tables need no ALTER migration."""
    db_path = tmp_path / "argus.db"
    first = connect(db_path)
    first.execute("CREATE TABLE IF NOT EXISTS legacy (id INTEGER PRIMARY KEY)")
    first.commit()
    first.close()

    second = connect(db_path)
    init_schema(second)
    rows = second.execute("SELECT COUNT(*) AS n FROM ingest_jobs").fetchone()
    second.close()

    assert rows["n"] == 0


def test_create_job_records_its_items_as_queued(conn: sqlite3.Connection) -> None:
    job_id = store.create_job(
        conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md", "b.pdf"]
    )

    job = store.get_job(conn, job_id)

    assert job is not None
    assert job["status"] == "queued"
    assert job["total"] == 2
    assert job["done"] == 0
    assert [item["filename"] for item in job["items"]] == ["a.md", "b.pdf"]
    assert {item["stage"] for item in job["items"]} == {"queued"}
    assert all(item["chunks"] == 0 and item["path"] is None for item in job["items"])


def test_advance_item_moves_one_row_through_its_stages(conn: sqlite3.Connection) -> None:
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])
    item_id = store.get_job(conn, job_id)["items"][0]["id"]

    store.advance_item(conn, item_id, stage="saving")
    store.advance_item(conn, item_id, stage="indexing", path="t/a.md")
    store.advance_item(conn, item_id, stage="done", chunks=4)

    item = store.get_job(conn, job_id)["items"][0]
    assert item["stage"] == "done"
    assert item["path"] == "t/a.md", "an earlier stage's path must not be dropped"
    assert item["chunks"] == 4


def test_advance_item_bumps_the_jobs_done_counter_only_on_terminal_stages(
    conn: sqlite3.Connection,
) -> None:
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md", "b.md"])
    items = store.get_job(conn, job_id)["items"]

    store.advance_item(conn, items[0]["id"], stage="indexing")
    assert store.get_job(conn, job_id)["done"] == 0

    store.advance_item(conn, items[0]["id"], stage="done")
    store.advance_item(conn, items[1]["id"], stage="skipped")
    assert store.get_job(conn, job_id)["done"] == 2


def test_finish_job_records_status_and_a_finish_time(conn: sqlite3.Connection) -> None:
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])

    store.start_job(conn, job_id)
    assert store.get_job(conn, job_id)["status"] == "running"

    store.finish_job(conn, job_id, status="partial", error="one file failed")
    job = store.get_job(conn, job_id)

    assert job["status"] == "partial"
    assert job["error"] == "one file failed"
    assert job["finished_at"]


def test_list_jobs_is_newest_first_and_carries_no_items(conn: sqlite3.Connection) -> None:
    first = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])
    second = store.create_job(conn, target="t", summary_prompt="", filenames=["b.md"])

    listed = store.list_jobs(conn)

    assert [job["id"] for job in listed][:2] == [second, first]
    assert "items" not in listed[0], "the list view must not fan out per job"


def test_get_job_returns_none_for_an_unknown_id(conn: sqlite3.Connection) -> None:
    assert store.get_job(conn, "nope") is None


def test_deleting_a_job_cascades_to_its_items(conn: sqlite3.Connection) -> None:
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])

    conn.execute("DELETE FROM ingest_jobs WHERE id = ?", (job_id,))
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM ingest_job_items").fetchone()["n"] == 0


def test_reconcile_fails_jobs_left_running_by_a_dead_process(conn: sqlite3.Connection) -> None:
    """A killed process leaves 'running' rows that would otherwise poll forever."""
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md", "b.md"])
    store.start_job(conn, job_id)
    conn.execute("UPDATE ingest_jobs SET boot_id = 'a-previous-process'")
    conn.commit()

    store.reconcile_stale_jobs(conn)
    job = store.get_job(conn, job_id)

    assert job["status"] == "failed"
    assert job["error"] == "interrupted by restart"
    assert {item["stage"] for item in job["items"]} == {"failed"}


def test_reconcile_leaves_this_process_own_jobs_alone(conn: sqlite3.Connection) -> None:
    """Reconciliation is boot-scoped; running it must not kill a live job."""
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])
    store.start_job(conn, job_id)

    store.reconcile_stale_jobs(conn)

    assert store.get_job(conn, job_id)["status"] == "running"


def test_reconcile_does_not_touch_already_finished_jobs(conn: sqlite3.Connection) -> None:
    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])
    store.finish_job(conn, job_id, status="ok")
    conn.execute("UPDATE ingest_jobs SET boot_id = 'a-previous-process'")
    conn.commit()

    store.reconcile_stale_jobs(conn)

    assert store.get_job(conn, job_id)["status"] == "ok"


def test_running_job_id_reports_the_one_in_flight(conn: sqlite3.Connection) -> None:
    """The routes refuse a second job rather than racing the embedding model."""
    assert store.running_job_id(conn) is None

    job_id = store.create_job(conn, target="t", summary_prompt="", filenames=["a.md"])
    assert store.running_job_id(conn) == job_id, "a queued job already holds the slot"

    store.finish_job(conn, job_id, status="ok")
    assert store.running_job_id(conn) is None


def test_note_style_round_trips(conn):
    job_id = store.create_job(
        conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md"], note_style="cornell"
    )
    assert store.get_job(conn, job_id)["note_style"] == "cornell"
    assert store.list_jobs(conn)[0]["note_style"] == "cornell"


def test_note_style_defaults_to_none_chosen(conn):
    job_id = store.create_job(conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md"])
    assert store.get_job(conn, job_id)["note_style"] == ""


def test_a_database_predating_note_style_is_migrated(tmp_path):
    """`init_schema` runs on every connection, so an existing 0.2 database has
    to grow the column rather than fail every ingest query with
    `no such column`. Simulated by dropping the column back off."""
    db_path = tmp_path / "argus.db"
    first = connect(db_path)
    init_schema(first)
    first.execute("ALTER TABLE ingest_jobs DROP COLUMN note_style")
    first.commit()
    first.close()

    second = connect(db_path)
    init_schema(second)
    try:
        columns = {row["name"] for row in second.execute("PRAGMA table_info(ingest_jobs)")}
        assert "note_style" in columns
        job_id = store.create_job(
            second, target="00-Inbox/files", summary_prompt="", filenames=["a.md"]
        )
        assert store.get_job(second, job_id)["note_style"] == ""
    finally:
        second.close()
