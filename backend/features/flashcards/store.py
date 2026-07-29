"""Flashcard decks parsed from the vault, scheduled with real FSRS.

Decks are generated from ``Q:: A::`` pairs in a course's
``15-Courses/<CODE>/flashcards.md`` note (mirrors ``backend/study/corpus.py``'s
vault-relative course layout) and persisted as a JSON blob
(``flashcard_decks.cards_json``), the same JSON-blob-column shape
``backend/study/practice_exam.py`` uses for ``exams.questions_json``.

Per-card scheduling state is normalized activity, one row per grading event
in ``flashcard_reviews`` — mirroring ``attempts`` for exams. The **latest**
row per ``card_id`` is a card's current FSRS state; a card with no review row
yet is "new" and due immediately.

Scheduling itself is delegated to ``fsrs`` (PyPI: ``fsrs``, the maintained
reference implementation of the Free Spaced Repetition Scheduler) rather than
a hand-rolled reimplementation of the published FSRS update rules.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler, State
from pydantic import BaseModel

COURSES_DIR = "15-Courses"

GRADE_TO_RATING: dict[str, Rating] = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


class FlashcardsError(RuntimeError):
    """Raised when a deck/card cannot be found, parsed, or graded."""


class DeckSummary(BaseModel):
    """One generated deck (list view — card count only, not full content)."""

    id: int
    course: str
    title: str
    created_at: str
    cards: int


class DueCard(BaseModel):
    """A card that is due for review, with its current FSRS state label."""

    id: str
    front: str
    back: str
    due_at: str
    state: str


class GradeResult(BaseModel):
    """FSRS state after grading one card."""

    card_id: str
    grade: str
    stability: float
    difficulty: float
    due_at: str
    state: str


def parse_qa_pairs(text: str) -> list[tuple[str, str]]:
    """Parse ``Q:: <front>`` / ``A:: <back>`` pairs from markdown.

    Each field may continue on following lines up to the next ``Q::``
    marker (or end of text). Pairs missing either half are dropped.
    """
    pairs: list[tuple[str, str]] = []
    front_lines: list[str] | None = None
    back_lines: list[str] | None = None
    mode: str | None = None

    def flush() -> None:
        if front_lines is not None and back_lines is not None:
            front = "\n".join(front_lines).strip()
            back = "\n".join(back_lines).strip()
            if front and back:
                pairs.append((front, back))

    for line in text.splitlines():
        if line.startswith("Q::"):
            flush()
            front_lines = [line[3:].strip()]
            back_lines = None
            mode = "q"
        elif line.startswith("A::"):
            back_lines = [line[3:].strip()]
            mode = "a"
        elif mode == "q" and front_lines is not None:
            front_lines.append(line)
        elif mode == "a" and back_lines is not None:
            back_lines.append(line)
    flush()
    return pairs


def _flashcards_path(vault_path: Path, course: str) -> Path:
    return vault_path / COURSES_DIR / course / "flashcards.md"


def generate_deck(vault_path: Path, conn: sqlite3.Connection, course: str) -> int:
    """Parse ``flashcards.md`` for ``course`` and persist a new deck.

    Returns the new ``flashcard_decks.id``. Raises :class:`FlashcardsError`
    if the file is missing or has no valid ``Q:: A::`` pairs.
    """
    path = _flashcards_path(vault_path, course)
    if not path.is_file():
        raise FlashcardsError(f"no flashcards.md for course {course}")
    pairs = parse_qa_pairs(path.read_text(encoding="utf-8"))
    if not pairs:
        raise FlashcardsError(f"no Q:: A:: pairs found in {path}")

    cursor = conn.execute(
        "INSERT INTO flashcard_decks (course, title, cards_json) VALUES (?, ?, ?)",
        (course, f"{course} flashcards", "[]"),
    )
    conn.commit()
    deck_id = int(cursor.lastrowid)
    cards = [
        {"id": f"{deck_id}:{index}", "front": front, "back": back}
        for index, (front, back) in enumerate(pairs)
    ]
    conn.execute(
        "UPDATE flashcard_decks SET cards_json = ? WHERE id = ?", (json.dumps(cards), deck_id)
    )
    conn.commit()
    return deck_id


def load_deck(conn: sqlite3.Connection, deck_id: int) -> dict:
    """Full deck record (id, course, title, created_at, cards)."""
    row = conn.execute("SELECT * FROM flashcard_decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise FlashcardsError(f"no flashcard deck {deck_id}")
    return {
        "id": row["id"],
        "course": row["course"],
        "title": row["title"],
        "created_at": row["created_at"],
        "cards": json.loads(row["cards_json"]),
    }


def list_decks(conn: sqlite3.Connection, course: str | None = None) -> list[DeckSummary]:
    """All decks, newest first, optionally scoped to one course."""
    rows = conn.execute(
        "SELECT id, course, title, created_at, cards_json FROM flashcard_decks"
        + (" WHERE course = ?" if course else "")
        + " ORDER BY id DESC",
        (course,) if course else (),
    ).fetchall()
    return [
        DeckSummary(
            id=row["id"],
            course=row["course"],
            title=row["title"],
            created_at=row["created_at"],
            cards=len(json.loads(row["cards_json"])),
        )
        for row in rows
    ]


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _latest_reviews(conn: sqlite3.Connection, deck_id: int) -> dict[str, sqlite3.Row]:
    """Latest ``flashcard_reviews`` row per ``card_id`` for one deck."""
    rows = conn.execute(
        """
        SELECT r.* FROM flashcard_reviews r
        JOIN (
            SELECT card_id, MAX(id) AS max_id
            FROM flashcard_reviews
            WHERE deck_id = ?
            GROUP BY card_id
        ) latest ON r.id = latest.max_id
        WHERE r.deck_id = ?
        """,
        (deck_id, deck_id),
    ).fetchall()
    return {row["card_id"]: row for row in rows}


def due_cards(conn: sqlite3.Connection, deck_id: int, now: datetime | None = None) -> list[DueCard]:
    """Cards in ``deck_id`` due for review, soonest-due first.

    A card with no review row yet is "new" and due as of deck creation
    (i.e. immediately).
    """
    now = now or datetime.now(timezone.utc)
    deck = load_deck(conn, deck_id)
    latest = _latest_reviews(conn, deck_id)
    deck_created = _parse_dt(deck["created_at"])

    scored: list[tuple[datetime, DueCard]] = []
    for card in deck["cards"]:
        row = latest.get(card["id"])
        if row is None:
            due_dt, due_at, state = deck_created, deck["created_at"], "New"
        else:
            due_at = row["due_at"]
            due_dt = _parse_dt(due_at)
            state = State(row["state"]).name
        if due_dt <= now:
            scored.append((due_dt, DueCard(id=card["id"], front=card["front"], back=card["back"], due_at=due_at, state=state)))

    scored.sort(key=lambda pair: pair[0])
    return [item for _, item in scored]


def grade_card(
    conn: sqlite3.Connection,
    deck_id: int,
    card_id: str,
    grade: str,
    now: datetime | None = None,
) -> GradeResult:
    """Apply an FSRS review to one card and persist the new state.

    Raises :class:`FlashcardsError` if the deck/card doesn't exist or
    ``grade`` isn't one of again/hard/good/easy.
    """
    if grade not in GRADE_TO_RATING:
        raise FlashcardsError(f"invalid grade {grade!r} — expected again/hard/good/easy")
    deck = load_deck(conn, deck_id)
    if not any(card["id"] == card_id for card in deck["cards"]):
        raise FlashcardsError(f"no card {card_id} in deck {deck_id}")

    now = now or datetime.now(timezone.utc)
    row = _latest_reviews(conn, deck_id).get(card_id)
    if row is None:
        card = FsrsCard()
    else:
        card = FsrsCard(
            state=State(row["state"]),
            step=row["step"],
            stability=row["stability"],
            difficulty=row["difficulty"],
            due=_parse_dt(row["due_at"]),
            last_review=_parse_dt(row["last_review_at"]) if row["last_review_at"] else None,
        )

    scheduler = Scheduler()
    new_card, _log = scheduler.review_card(card, GRADE_TO_RATING[grade], review_datetime=now)

    conn.execute(
        "INSERT INTO flashcard_reviews"
        " (card_id, deck_id, grade, state, step, stability, difficulty, due_at, last_review_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card_id,
            deck_id,
            grade,
            int(new_card.state),
            new_card.step,
            new_card.stability,
            new_card.difficulty,
            new_card.due.isoformat(),
            new_card.last_review.isoformat() if new_card.last_review else None,
        ),
    )
    conn.commit()

    return GradeResult(
        card_id=card_id,
        grade=grade,
        stability=new_card.stability,
        difficulty=new_card.difficulty,
        due_at=new_card.due.isoformat(),
        state=State(new_card.state).name,
    )
