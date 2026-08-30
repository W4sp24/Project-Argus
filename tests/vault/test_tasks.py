"""Tests for the Obsidian Tasks parser and bucket views."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.vault.tasks import advance_date, bucketed_tasks, parse_task_line, refresh_cache

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


# --- Recurrence (the Tasks plugin's 🔁 marker) --------------------------------


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("- [ ] Water plants 🔁 every day", "every day"),
        ("- [ ] Water plants 🔁 every 2 days", "every 2 days"),
        ("- [ ] Water plants 🔁 every week 📅 2026-09-06", "every week"),
        ("- [ ] Water plants 🔁 every 3 weeks ⏫", "every 3 weeks"),
        ("- [ ] Pay rent 🔁 every month 📅 2026-09-01 #home", "every month"),
        ("- [ ] Book the MOT 🔁 every year ✅ 2026-09-01", "every year"),
        ("- [ ] Standup 🔁 every monday ⏳ 2026-09-07", "every monday"),
        ("- [ ] Standup [repeat: every week] #work", "every week"),
    ],
)
def test_parse_recurrence_marker(line: str, rule: str) -> None:
    task = parse_task_line(line)
    assert task is not None
    assert task.recurrence == rule


def test_recurrence_is_stripped_from_the_rendered_text() -> None:
    """The panel renders `text`; leaving the rule in it shows the user
    "Water plants 🔁 every week" where they wrote a task called "Water plants"."""
    task = parse_task_line("- [ ] Water plants 🔁 every week 📅 2026-09-06 ⏫ #home")
    assert task is not None
    assert task.text == "Water plants"
    assert task.recurrence == "every week"
    # The rest of the parse is unchanged by the new marker.
    assert task.due == "2026-09-06"
    assert task.priority == "high"
    assert task.tags == ["home"]


def test_bracket_recurrence_is_stripped_from_the_rendered_text() -> None:
    task = parse_task_line("- [ ] Standup [repeat: every week] [due: 2026-09-07]")
    assert task is not None
    assert task.text == "Standup"
    assert task.recurrence == "every week"
    assert task.due == "2026-09-07"


def test_a_task_without_a_recurrence_marker_has_none() -> None:
    """Additive field: every task written before 🔁 existed still parses."""
    task = parse_task_line("- [ ] Renew passport 📅 2026-07-20 ⏫ #areas/admin")
    assert task is not None
    assert task.recurrence is None


def test_a_bare_recurrence_marker_is_not_a_rule() -> None:
    """🔁 with nothing after it must not capture the following marker."""
    task = parse_task_line("- [ ] Water plants 🔁 📅 2026-09-06")
    assert task is not None
    assert task.recurrence is None
    assert task.due == "2026-09-06"


@pytest.mark.parametrize(
    ("anchor", "rule", "expected"),
    [
        ("2026-09-06", "every day", "2026-09-07"),
        ("2026-09-06", "every 3 days", "2026-09-09"),
        ("2026-09-06", "every week", "2026-09-13"),
        ("2026-09-06", "every 2 weeks", "2026-09-20"),
        ("2026-09-06", "every month", "2026-10-06"),
        ("2026-09-06", "every 4 months", "2027-01-06"),
        ("2026-09-06", "every year", "2027-09-06"),
        # Month-end: there is no 31 February. A monthly task must land in the
        # next month rather than skipping one, so the day clamps.
        ("2026-01-31", "every month", "2026-02-28"),
        ("2026-12-31", "every month", "2027-01-31"),
        ("2024-02-29", "every year", "2025-02-28"),
        # A weekday rule anchored on that same weekday means *next* week --
        # advancing by zero days would make the task un-completable.
        ("2026-09-07", "every monday", "2026-09-14"),
        ("2026-09-06", "every monday", "2026-09-07"),
        ("2026-09-06", "EVERY   Friday", "2026-09-11"),
    ],
)
def test_advance_date(anchor: str, rule: str, expected: str) -> None:
    assert advance_date(anchor, rule) == expected


@pytest.mark.parametrize(
    "rule",
    ["every blue moon", "", "weekly", "every", "every 2 mondays", "every week on tuesday"],
)
def test_advance_date_returns_none_for_an_unrecognised_rule(rule: str) -> None:
    """None, never an exception: the caller degrades to a plain toggle."""
    assert advance_date("2026-09-06", rule) is None


def test_advance_date_returns_none_for_an_impossible_anchor() -> None:
    assert advance_date("2026-02-31", "every week") is None
