"""Tests for the Obsidian Tasks parser and bucket views."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from backend.db import connect, init_schema
from backend.tasks.parser import bucketed_tasks, parse_task_line, refresh_cache

TODAY = date(2026, 7, 12)


def test_parse_emoji_markers() -> None:
    task = parse_task_line("- [ ] Renew passport 📅 2026-07-20 ⏫ #areas/admin")
    assert task is not None
    assert task.text == "Renew passport"
    assert task.due == "2026-07-20"
    assert task.priority == "high"
    assert task.tags == ["areas/admin"]
    assert task.done is False


def test_parse_bracket_fallbacks_and_done() -> None:
    task = parse_task_line(
        "- [x] Read chapter 4 [due: 2026-07-18] [prio: low] #cs201 ✅ 2026-07-10"
    )
    assert task is not None
    assert task.done is True
    assert task.due == "2026-07-18"
    assert task.priority == "low"
    assert task.tags == ["cs201"]
    assert "✅" not in task.text and "[due" not in task.text


def test_parse_scheduled_and_non_tasks() -> None:
    task = parse_task_line("* [ ] Draft essay ⏳ 2026-07-14")
    assert task is not None and task.scheduled == "2026-07-14"
    assert parse_task_line("just prose with a #tag") is None
    assert parse_task_line("- normal bullet") is None


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "friday.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_refresh_cache_and_buckets(conn: sqlite3.Connection, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "10-Daily").mkdir(parents=True)
    (vault / "99-Private").mkdir()
    (vault / "10-Daily" / "2026-07-12.md").write_text(
        "- [ ] Overdue thing 📅 2026-07-10\n"
        "- [ ] Due today 📅 2026-07-12 🔼\n"
        "- [ ] This week 📅 2026-07-16\n"
        "- [ ] Far future 📅 2026-09-01\n"
        "- [ ] No date at all\n"
        "- [x] Already done 📅 2026-07-12\n",
        encoding="utf-8",
    )
    (vault / "99-Private" / "secret.md").write_text(
        "- [ ] Private task 📅 2026-07-12\n", encoding="utf-8"
    )

    open_count = refresh_cache(conn, vault)
    assert open_count == 5, "done tasks excluded from open count"

    buckets = bucketed_tasks(conn, today=TODAY)
    assert [task.text for task in buckets["overdue"]] == ["Overdue thing"]
    assert [task.text for task in buckets["today"]] == ["Due today"]
    assert [task.text for task in buckets["week"]] == ["This week"]
    assert {task.text for task in buckets["someday"]} == {"Far future", "No date at all"}
    all_texts = [task.text for bucket in buckets.values() for task in bucket]
    assert "Private task" not in all_texts, "I3 violation: private task cached"
