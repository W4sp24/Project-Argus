"""Delete study data — course folders, and the DB rows a course leaves behind.

Deleting a course (in Obsidian by hand, or via the courses panel's × button)
used to leave the ``exams``/``attempts``/``flashcard_decks``/``flashcard_reviews``
rows behind: nothing in this feature ever removed them, so generated exams and
decks kept listing on ``/study/exam`` and ``/study/flashcards`` long after
their course was gone — the reported "Study … still retains sample data after
it is deleted" bug.

``backend/core/db.py``'s ``connect()`` runs with ``PRAGMA foreign_keys=ON``,
and none of these tables use ``ON DELETE CASCADE`` (adding it now would mean a
full table rebuild for every already-installed database — see db.py's own
migration comments for why that's treated as expensive here) — so children
are always deleted before parents: ``attempts`` before ``exams``,
``flashcard_reviews`` before ``flashcard_decks``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.study.practice_exam import StudyError
from backend.vault.writer import WriterMissing, delete_course_tree


@dataclass
class CourseDeleteResult:
    """What actually got removed by :func:`delete_course` — for a truthful UI toast."""

    exams_removed: int
    attempts_removed: int
    decks_removed: int
    reviews_removed: int
    folder_removed: bool


def delete_exam(conn: sqlite3.Connection, exam_id: int) -> int:
    """Delete one exam and its attempts (children first). Returns attempts removed."""
    exists = conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if exists is None:
        raise StudyError(f"no exam with id {exam_id}")
    attempts_removed = conn.execute("DELETE FROM attempts WHERE exam_id = ?", (exam_id,)).rowcount
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    return attempts_removed


def delete_course(
    conn: sqlite3.Connection,
    vault_path: Path,
    code: str,
    *,
    purge: bool,
    taxonomy: Taxonomy | None = None,
) -> CourseDeleteResult:
    """Remove a course's exam/flashcard rows, and — when ``purge`` — its vault folder.

    The vault folder (when ``purge``) is deleted *before* any database row is
    touched: :func:`~backend.vault.writer.delete_course_tree` refuses a bad
    ``code`` (traversal, or a code that isn't a direct child of the courses
    dir) outright, so a rejected request leaves nothing changed rather than
    leaving DB rows gone and the folder untouched.

    ``purge=False`` is for a course whose folder the user already deleted by
    hand (e.g. in Obsidian) — this only cleans up the orphaned DB rows that
    left behind.
    """
    tax = taxonomy or active_taxonomy()

    folder_removed = False
    if purge:
        try:
            delete_course_tree(vault_path, code, taxonomy=tax)
            folder_removed = True
        except WriterMissing:
            # Already gone from the vault — exactly the case purge=False
            # exists to clean up after, not an error here either.
            folder_removed = False

    exam_ids = [row["id"] for row in conn.execute("SELECT id FROM exams WHERE course = ?", (code,))]
    attempts_removed = 0
    if exam_ids:
        placeholders = ",".join("?" * len(exam_ids))
        attempts_removed = conn.execute(
            f"DELETE FROM attempts WHERE exam_id IN ({placeholders})", exam_ids
        ).rowcount
    exams_removed = conn.execute("DELETE FROM exams WHERE course = ?", (code,)).rowcount

    deck_rows = conn.execute("SELECT id FROM flashcard_decks WHERE course = ?", (code,))
    deck_ids = [row["id"] for row in deck_rows]
    reviews_removed = 0
    if deck_ids:
        placeholders = ",".join("?" * len(deck_ids))
        reviews_removed = conn.execute(
            f"DELETE FROM flashcard_reviews WHERE deck_id IN ({placeholders})", deck_ids
        ).rowcount
    decks_removed = conn.execute("DELETE FROM flashcard_decks WHERE course = ?", (code,)).rowcount

    conn.commit()

    return CourseDeleteResult(
        exams_removed=exams_removed,
        attempts_removed=attempts_removed,
        decks_removed=decks_removed,
        reviews_removed=reviews_removed,
        folder_removed=folder_removed,
    )
