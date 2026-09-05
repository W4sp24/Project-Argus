"""Tests for the Obsidian Tasks parser and bucket views."""

import sqlite3
import time
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


def test_recurrence_survives_the_cache(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A recurring task must still look recurring after the cache round-trip.

    `parse_task_line` reads 🔁 straight off the markdown, but nothing the user
    sees comes from there: the agenda and the board both read `tasks_cache`,
    which `refresh_cache` rebuilds. Before the column existed, `recurrence`
    was populated on a fresh parse and silently `None` everywhere it was
    rendered -- the badge would have been dead on arrival, and the difference
    is invisible unless the assertion goes through the cache like this one.
    """
    vault = tmp_path / "vault"
    (vault / "10-Daily").mkdir(parents=True)
    (vault / "10-Daily" / "2026-07-12.md").write_text(
        "- [ ] Water the plants 🔁 every week 📅 2026-07-12\n"
        "- [ ] One-off errand 📅 2026-07-12\n",
        encoding="utf-8",
    )

    refresh_cache(conn, vault)
    today_tasks = {task.text: task for task in bucketed_tasks(conn, today=TODAY)["today"]}

    assert today_tasks["Water the plants"].recurrence == "every week"
    assert today_tasks["One-off errand"].recurrence is None
    # The rule is metadata, not part of the title the panel prints.
    assert "🔁" not in today_tasks["Water the plants"].text


# --- the cache is incremental ------------------------------------------------


def _aged(path: Path) -> None:
    """Backdate a file past RECENT_EDIT_SECONDS.

    The scan always re-reads anything touched in the last couple of seconds,
    because coarse filesystem timestamps could otherwise hide a same-tick edit
    of the same length. A test writing files milliseconds ago would trip that
    guard and see a full re-read every time, proving nothing.
    """
    import os

    old = time.time() - 60
    os.utime(path, (old, old))


def test_a_second_refresh_reads_nothing_that_has_not_changed(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is called from /api/agenda, /api/tasks, insights, briefing and the
    `list_tasks` chat tool. It used to read every markdown file in the vault on
    every one of them, so opening the dashboard cost a full vault scan."""
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    for name in ("a", "b", "c"):
        note = vault / "20-Projects" / f"{name}.md"
        note.write_text(f"- [ ] task in {name}\n", encoding="utf-8")
        _aged(note)

    assert refresh_cache(conn, vault) == 3

    reads: list[str] = []
    original = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        reads.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    assert refresh_cache(conn, vault) == 3, "the count still comes out of the cache"
    assert reads == [], f"nothing changed, so nothing should have been read: {reads}"


def test_an_edit_an_addition_and_a_deletion_all_still_land(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    kept = vault / "20-Projects" / "kept.md"
    doomed = vault / "20-Projects" / "doomed.md"
    kept.write_text("- [ ] original\n", encoding="utf-8")
    doomed.write_text("- [ ] goes away\n", encoding="utf-8")
    _aged(kept)
    _aged(doomed)

    assert refresh_cache(conn, vault) == 2

    kept.write_text("- [ ] edited\n- [ ] and a second one\n", encoding="utf-8")
    added = vault / "20-Projects" / "added.md"
    added.write_text("- [ ] brand new\n", encoding="utf-8")
    doomed.unlink()

    assert refresh_cache(conn, vault) == 3

    texts = {row["text"] for row in conn.execute("SELECT text FROM tasks_cache")}
    assert texts == {"edited", "and a second one", "brand new"}
    fingerprints = {row["path"] for row in conn.execute("SELECT path FROM tasks_cache_files")}
    assert fingerprints == {"20-Projects/kept.md", "20-Projects/added.md"}


def test_a_same_size_edit_within_the_grace_window_is_not_missed(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A note rewritten to the same length in the same filesystem tick has an
    identical (mtime_ns, size) fingerprint. RECENT_EDIT_SECONDS is what stops
    that from being read as "unchanged"."""
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("- [ ] aaaa\n", encoding="utf-8")

    assert refresh_cache(conn, vault) == 1

    note.write_text("- [ ] bbbb\n", encoding="utf-8")  # same length, just written
    refresh_cache(conn, vault)

    assert [row["text"] for row in conn.execute("SELECT text FROM tasks_cache")] == ["bbbb"]


def test_a_file_the_taxonomy_excludes_is_never_read(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """I3: the private zone must not be scanned, incrementally or otherwise."""
    vault = tmp_path / "vault"
    (vault / "99-Private").mkdir(parents=True)
    (vault / "99-Private" / "diary.md").write_text("- [ ] secret plan\n", encoding="utf-8")

    assert refresh_cache(conn, vault) == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks_cache_files").fetchone()[0] == 0
