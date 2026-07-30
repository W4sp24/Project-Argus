"""Tests for course deletion — the actual reported bug: deleted study data
(sample or otherwise) kept reappearing because nothing removed the DB rows
or the vault folder for real."""

import json
import subprocess
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.study.deletes import delete_course, delete_exam
from backend.vault.writer import WriterError, WriterForbidden


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "15-Courses" / "CS201" / "materials").mkdir(parents=True)
    (root / "15-Courses" / "CS201" / "course.md").write_text(
        "---\ntitle: X\n---\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    return _vault(tmp_path)


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def _seed_exam_and_attempt(conn, course: str = "CS201") -> tuple[int, int]:
    cursor = conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES (?, ?, ?)",
        (course, f"{course} exam", json.dumps({"questions": []})),
    )
    conn.commit()
    exam_id = int(cursor.lastrowid)
    cursor = conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json) VALUES (?, ?, ?, ?)",
        (exam_id, 1, 2, "[]"),
    )
    conn.commit()
    return exam_id, int(cursor.lastrowid)


def _seed_deck_and_review(conn, course: str = "CS201") -> tuple[int, int]:
    cursor = conn.execute(
        "INSERT INTO flashcard_decks (course, title, cards_json) VALUES (?, ?, ?)",
        (course, f"{course} flashcards", json.dumps([{"id": "1:0", "front": "f", "back": "b"}])),
    )
    conn.commit()
    deck_id = int(cursor.lastrowid)
    cursor = conn.execute(
        "INSERT INTO flashcard_reviews"
        " (card_id, deck_id, grade, state, step, stability, difficulty, due_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("1:0", deck_id, "good", 2, None, 1.0, 1.0, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    return deck_id, int(cursor.lastrowid)


# --- delete_course --------------------------------------------------------


def test_delete_course_removes_exam_attempt_deck_and_review_rows(conn, vault: Path) -> None:
    exam_id, _ = _seed_exam_and_attempt(conn)
    deck_id, _ = _seed_deck_and_review(conn)

    result = delete_course(conn, vault, "CS201", purge=False)

    assert result.exams_removed == 1
    assert result.attempts_removed == 1
    assert result.decks_removed == 1
    assert result.reviews_removed == 1
    assert conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone() is None
    assert conn.execute("SELECT * FROM attempts").fetchone() is None
    assert conn.execute("SELECT * FROM flashcard_decks WHERE id = ?", (deck_id,)).fetchone() is None
    assert conn.execute("SELECT * FROM flashcard_reviews").fetchone() is None


def test_delete_course_purge_true_removes_folder_with_a_prior_snapshot(conn, vault: Path) -> None:
    course_dir = vault / "15-Courses" / "CS201"
    before = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout.count("\n")

    result = delete_course(conn, vault, "CS201", purge=True)

    assert result.folder_removed is True
    assert not course_dir.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert log.count("\n") == before + 1, "I2 violation: no pre-apply commit before the rmtree"
    assert "delete course CS201" in log


def test_delete_course_purge_false_leaves_the_folder_alone(conn, vault: Path) -> None:
    course_dir = vault / "15-Courses" / "CS201"

    result = delete_course(conn, vault, "CS201", purge=False)

    assert result.folder_removed is False
    assert course_dir.is_dir(), "purge=False must never touch the vault"


def test_delete_course_purge_true_on_an_already_deleted_folder_only_cleans_db(
    conn, vault: Path
) -> None:
    """The other half of purge: a course whose folder the user already
    deleted by hand (in Obsidian) must not raise — that IS the cleanup case."""
    import shutil

    shutil.rmtree(vault / "15-Courses" / "CS201")
    _seed_exam_and_attempt(conn)

    result = delete_course(conn, vault, "CS201", purge=True)

    assert result.folder_removed is False
    assert result.exams_removed == 1


def test_delete_course_refuses_a_code_outside_the_courses_dir(conn, vault: Path) -> None:
    (vault / "20-Projects").mkdir()
    with pytest.raises(WriterError):
        delete_course(conn, vault, "../20-Projects", purge=True)


def test_delete_course_refuses_traversal(conn, vault: Path) -> None:
    with pytest.raises(WriterForbidden):
        delete_course(conn, vault, "../escape", purge=True)


def test_delete_course_bad_code_leaves_db_rows_untouched(conn, vault: Path) -> None:
    """A refused purge must not have deleted DB rows on its way to failing."""
    _seed_exam_and_attempt(conn)
    with pytest.raises(WriterError):
        delete_course(conn, vault, "../escape", purge=True)
    assert conn.execute("SELECT * FROM exams").fetchone() is not None


def test_delete_course_purge_false_never_calls_the_writer_so_a_bad_code_is_a_no_op(
    conn, vault: Path
) -> None:
    """purge=False only ever touches the DB — an odd course string is just a
    course nothing matches, not a path the writer needs to validate."""
    result = delete_course(conn, vault, "../escape", purge=False)
    assert result.folder_removed is False
    assert result.exams_removed == 0


# --- delete_exam -----------------------------------------------------------


def test_delete_exam_removes_exam_and_its_attempts(conn) -> None:
    exam_id, _ = _seed_exam_and_attempt(conn)

    attempts_removed = delete_exam(conn, exam_id)

    assert attempts_removed == 1
    assert conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone() is None
    assert conn.execute("SELECT * FROM attempts").fetchone() is None


def test_delete_exam_missing_raises(conn) -> None:
    from backend.features.study.practice_exam import StudyError

    with pytest.raises(StudyError):
        delete_exam(conn, 99999)
