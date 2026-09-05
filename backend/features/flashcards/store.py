"""Decks and cards, as rows you can author.

A deck used to be a course name plus a JSON blob of cards parsed once from
``<courses>/<CODE>/flashcards.md`` — a file nothing in Argus ever wrote. That
made the whole feature unreachable unless you hand-authored it, and made every
card immutable once created.

Now a deck is a row you can rename and describe, belonging to a course or to
nothing at all (``course = ''``), holding ``flashcard_cards`` rows you can add,
edit, reorder, star and suspend. Where the cards *came* from is a separate
question with four answers, none of which is privileged: typed in
(``add_cards``), pasted (``parsing.parse_delimited``), imported from any vault
note (:mod:`backend.features.flashcards.vault`), or generated from the corpus
(:mod:`backend.features.flashcards.generate`).

Scheduling still belongs to :mod:`backend.features.flashcards.scheduler`, and
per-card state is still normalized activity: one ``flashcard_reviews`` row per
grading event, latest row per ``card_id`` wins. Cards are keyed for review by
``card_ref``, a string, because that is what let the migration from the blob
preserve every review ever recorded — see
``backend.core.db._migrate_flashcard_cards``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.features.flashcards import scheduler
from backend.features.flashcards.scheduler import GradeResult, SchedulerError

__all__ = [
    "CardInfo",
    "DeckDetail",
    "DeckDueSummary",
    "DeckSummary",
    "DueCard",
    "DueSummary",
    "FlashcardsError",
    "GradeResult",
    "add_cards",
    "best_match_score",
    "create_deck",
    "delete_card",
    "delete_deck",
    "due_cards",
    "due_summary",
    "grade_card",
    "list_decks",
    "load_deck",
    "record_match_score",
    "reorder_cards",
    "update_card",
    "update_deck",
]


class FlashcardsError(RuntimeError):
    """Raised when a deck/card cannot be found, parsed, or graded."""


class DeckSummary(BaseModel):
    """One deck in the library view — counts, not content."""

    id: int
    course: str
    title: str
    description: str
    source: str
    #: Which files this deck was written from, vault-relative. Empty for a deck
    #: nobody generated -- and for one generated before the column existed.
    #: Not user-editable: it is a fact about a generation, not a preference.
    source_paths: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    cards: int


class CardInfo(BaseModel):
    """One card, as the editor and the study activities see it."""

    ref: str
    front: str
    back: str
    hint: str | None
    position: int
    starred: bool
    suspended: bool
    source_path: str | None


class DeckDetail(DeckSummary):
    """A deck with all of its cards, ordered."""

    card_list: list[CardInfo]


class DueCard(BaseModel):
    """A card due for review, with its FSRS state and what each grade costs."""

    id: str
    front: str
    back: str
    hint: str | None
    due_at: str
    state: str
    #: `{again, hard, good, easy}` -> a human interval. Computed, not stored.
    preview: dict[str, str]


class DeckDueSummary(BaseModel):
    """One deck's due-card count, as part of the whole-vault summary."""

    deck_id: int
    course: str
    title: str
    due: int


class DueSummary(BaseModel):
    """Cards due across every deck, in one request.

    ``useDueCards()`` only takes a single deck id, so a whole-vault total (the
    Notebook overview's stat row) would otherwise cost one request per deck.
    """

    total: int
    decks: list[DeckDueSummary]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_card_ref() -> str:
    """A card key that cannot collide with a migrated ``"{deck}:{index}"``."""
    return f"c{uuid.uuid4().hex}"


# --- decks -----------------------------------------------------------------


def create_deck(
    conn: sqlite3.Connection,
    *,
    title: str,
    course: str = "",
    description: str = "",
    source: str = "manual",
    source_paths: list[str] | None = None,
) -> int:
    """Create an empty deck. Returns its id.

    ``course=""`` is a deck that belongs to no course, which is the common case
    for one typed in by hand. It is a value rather than NULL because relaxing
    the column's NOT NULL would cost a table rebuild to buy nothing that ``''``
    cannot already say.

    ``source_paths`` is written once, here, because the deck row is created
    before the generation job runs -- so a generation that fails still leaves a
    deck that knows what it was asked to read.
    """
    clean = title.strip()
    if not clean:
        raise FlashcardsError("a deck needs a title")
    cursor = conn.execute(
        "INSERT INTO flashcard_decks"
        " (course, title, description, source, source_paths, cards_json, updated_at)"
        " VALUES (?, ?, ?, ?, ?, '[]', ?)",
        (
            course.strip(),
            clean,
            description.strip(),
            source,
            _encode_paths(source_paths),
            _now(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _encode_paths(paths: list[str] | None) -> str:
    """A deck's provenance list, as ``audit.paths_json`` stores the same thing.

    Sorted and de-duplicated so two generations over the same ticked files
    compare equal, and ``ensure_ascii=False`` so a non-ASCII filename stays
    readable in the database rather than becoming an escape sequence.
    """
    return json.dumps(sorted(set(paths or [])), ensure_ascii=False)


def _decode_paths(raw: str | None) -> list[str]:
    """Never raise: a deck row is not worth losing to a hand-edited column."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _deck_row(conn: sqlite3.Connection, deck_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM flashcard_decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise FlashcardsError(f"no flashcard deck {deck_id}")
    return row


def _summary(row: sqlite3.Row, cards: int) -> DeckSummary:
    return DeckSummary(
        id=row["id"],
        course=row["course"],
        title=row["title"],
        description=row["description"],
        source=row["source"],
        source_paths=_decode_paths(row["source_paths"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"] or row["created_at"],
        cards=cards,
    )


def _card_info(row: sqlite3.Row) -> CardInfo:
    return CardInfo(
        ref=row["card_ref"],
        front=row["front"],
        back=row["back"],
        hint=row["hint"],
        position=row["position"],
        starred=bool(row["starred"]),
        suspended=bool(row["suspended"]),
        source_path=row["source_path"],
    )


def load_deck(conn: sqlite3.Connection, deck_id: int) -> DeckDetail:
    """A deck and all of its cards, in author order."""
    row = _deck_row(conn, deck_id)
    cards = [
        _card_info(card)
        for card in conn.execute(
            "SELECT * FROM flashcard_cards WHERE deck_id = ? ORDER BY position, id", (deck_id,)
        )
    ]
    return DeckDetail(**_summary(row, len(cards)).model_dump(), card_list=cards)


def list_decks(conn: sqlite3.Connection, course: str | None = None) -> list[DeckSummary]:
    """All decks, newest first, optionally scoped to one course."""
    rows = conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM flashcard_cards c WHERE c.deck_id = d.id) AS n"
        " FROM flashcard_decks d"
        + (" WHERE d.course = ?" if course else "")
        + " ORDER BY d.id DESC",
        (course,) if course else (),
    ).fetchall()
    return [_summary(row, row["n"]) for row in rows]


def update_deck(
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    course: str | None = None,
) -> DeckSummary:
    """Rename, re-describe, or re-file a deck. Omitted fields are untouched."""
    _deck_row(conn, deck_id)
    sets: list[str] = []
    values: list[Any] = []
    if title is not None:
        clean = title.strip()
        if not clean:
            raise FlashcardsError("a deck needs a title")
        sets.append("title = ?")
        values.append(clean)
    if description is not None:
        sets.append("description = ?")
        values.append(description.strip())
    if course is not None:
        sets.append("course = ?")
        values.append(course.strip())
    if sets:
        sets.append("updated_at = ?")
        values.append(_now())
        conn.execute(
            f"UPDATE flashcard_decks SET {', '.join(sets)} WHERE id = ?", (*values, deck_id)
        )
        conn.commit()
    row = _deck_row(conn, deck_id)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
    ).fetchone()["n"]
    return _summary(row, count)


def delete_deck(conn: sqlite3.Connection, deck_id: int) -> int:
    """Delete one deck, its cards, its reviews and its scores. Returns reviews removed.

    Children first: ``connect()`` runs with ``PRAGMA foreign_keys=ON`` and none
    of these tables declares ``ON DELETE CASCADE``.
    """
    _deck_row(conn, deck_id)
    reviews_removed = conn.execute(
        "DELETE FROM flashcard_reviews WHERE deck_id = ?", (deck_id,)
    ).rowcount
    conn.execute("DELETE FROM flashcard_match_scores WHERE deck_id = ?", (deck_id,))
    conn.execute("DELETE FROM flashcard_cards WHERE deck_id = ?", (deck_id,))
    conn.execute("DELETE FROM flashcard_decks WHERE id = ?", (deck_id,))
    conn.commit()
    return reviews_removed


# --- cards -----------------------------------------------------------------


def add_cards(
    conn: sqlite3.Connection,
    deck_id: int,
    cards: list[dict[str, Any]],
    *,
    source_path: str | None = None,
) -> int:
    """Append cards to a deck. Returns how many were added.

    Cards missing either face are dropped rather than stored half-formed —
    every import path relies on this, so the count returned is what the user
    actually got.
    """
    _deck_row(conn, deck_id)
    start = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS p FROM flashcard_cards WHERE deck_id = ?",
        (deck_id,),
    ).fetchone()["p"]

    rows = []
    position = start
    for card in cards:
        front = str(card.get("front", "")).strip()
        back = str(card.get("back", "")).strip()
        if not front or not back:
            continue
        position += 1
        hint = card.get("hint")
        rows.append(
            (
                deck_id,
                new_card_ref(),
                front,
                back,
                str(hint).strip() if hint else None,
                position,
                card.get("source_path") or source_path,
            )
        )
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO flashcard_cards"
        " (deck_id, card_ref, front, back, hint, position, source_path)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute("UPDATE flashcard_decks SET updated_at = ? WHERE id = ?", (_now(), deck_id))
    conn.commit()
    return len(rows)


def _card_row(conn: sqlite3.Connection, deck_id: int, ref: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM flashcard_cards WHERE deck_id = ? AND card_ref = ?", (deck_id, ref)
    ).fetchone()
    if row is None:
        raise FlashcardsError(f"no card {ref} in deck {deck_id}")
    return row


def update_card(
    conn: sqlite3.Connection,
    deck_id: int,
    ref: str,
    *,
    front: str | None = None,
    back: str | None = None,
    hint: str | None = None,
    starred: bool | None = None,
    suspended: bool | None = None,
) -> CardInfo:
    """Edit one card. Omitted fields are untouched.

    ``hint=""`` clears the hint; ``hint=None`` leaves it alone. Front and back
    may not be blanked — a card with one face is not a card.
    """
    _card_row(conn, deck_id, ref)
    sets: list[str] = []
    values: list[Any] = []
    for column, value in (("front", front), ("back", back)):
        if value is None:
            continue
        clean = value.strip()
        if not clean:
            raise FlashcardsError(f"a card needs a {column}")
        sets.append(f"{column} = ?")
        values.append(clean)
    if hint is not None:
        sets.append("hint = ?")
        values.append(hint.strip() or None)
    if starred is not None:
        sets.append("starred = ?")
        values.append(int(starred))
    if suspended is not None:
        sets.append("suspended = ?")
        values.append(int(suspended))
    if sets:
        sets.append("updated_at = ?")
        values.append(_now())
        conn.execute(
            f"UPDATE flashcard_cards SET {', '.join(sets)} WHERE deck_id = ? AND card_ref = ?",
            (*values, deck_id, ref),
        )
        conn.execute("UPDATE flashcard_decks SET updated_at = ? WHERE id = ?", (_now(), deck_id))
        conn.commit()
    return _card_info(_card_row(conn, deck_id, ref))


def delete_card(conn: sqlite3.Connection, deck_id: int, ref: str) -> int:
    """Delete one card and its review history. Returns reviews removed.

    The reviews go too, deliberately: their ``card_id`` would otherwise point
    at nothing, and a later card reusing the ref -- which cannot happen for new
    refs, but can for a re-migrated legacy deck -- would inherit a stranger's
    schedule.
    """
    _card_row(conn, deck_id, ref)
    reviews_removed = conn.execute(
        "DELETE FROM flashcard_reviews WHERE deck_id = ? AND card_id = ?", (deck_id, ref)
    ).rowcount
    conn.execute("DELETE FROM flashcard_cards WHERE deck_id = ? AND card_ref = ?", (deck_id, ref))
    conn.execute("UPDATE flashcard_decks SET updated_at = ? WHERE id = ?", (_now(), deck_id))
    conn.commit()
    return reviews_removed


def reorder_cards(conn: sqlite3.Connection, deck_id: int, order: list[str]) -> None:
    """Rewrite card positions to match ``order``.

    ``order`` must name every card in the deck exactly once. A partial order is
    refused rather than applied: silently leaving unnamed cards at stale
    positions produces an order nobody asked for.
    """
    existing = [
        row["card_ref"]
        for row in conn.execute(
            "SELECT card_ref FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
        )
    ]
    if not existing:
        _deck_row(conn, deck_id)
    if sorted(order) != sorted(existing):
        raise FlashcardsError(
            f"order must name each of the deck's {len(existing)} cards exactly once"
        )
    conn.executemany(
        "UPDATE flashcard_cards SET position = ? WHERE deck_id = ? AND card_ref = ?",
        [(index, deck_id, ref) for index, ref in enumerate(order)],
    )
    conn.execute("UPDATE flashcard_decks SET updated_at = ? WHERE id = ?", (_now(), deck_id))
    conn.commit()


# --- scheduling ------------------------------------------------------------


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


def _state_of(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "state": row["state"],
        "step": row["step"],
        "stability": row["stability"],
        "difficulty": row["difficulty"],
        "due_at": row["due_at"],
        "last_review_at": row["last_review_at"],
    }


def due_cards(
    conn: sqlite3.Connection, deck_id: int, now: datetime | None = None
) -> list[DueCard]:
    """Cards due for review, soonest-due first.

    A card with no review row yet is new and due as of deck creation, i.e.
    immediately. A suspended card is never due — that is what suspending is.
    """
    at = now or datetime.now(UTC)
    deck = _deck_row(conn, deck_id)
    latest = _latest_reviews(conn, deck_id)
    created = scheduler.parse_dt(deck["created_at"])

    scored: list[tuple[datetime, DueCard]] = []
    for row in conn.execute(
        "SELECT * FROM flashcard_cards WHERE deck_id = ? AND suspended = 0 ORDER BY position, id",
        (deck_id,),
    ):
        review_row = latest.get(row["card_ref"])
        state = _state_of(review_row)
        if review_row is None:
            due_dt, due_at, label = created, deck["created_at"], "New"
        else:
            due_at = review_row["due_at"]
            due_dt = scheduler.parse_dt(due_at)
            label = scheduler.State(review_row["state"]).name
        if due_dt > at:
            continue
        scored.append(
            (
                due_dt,
                DueCard(
                    id=row["card_ref"],
                    front=row["front"],
                    back=row["back"],
                    hint=row["hint"],
                    due_at=due_at,
                    state=label,
                    preview=scheduler.preview(state, at),
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0])
    return [card for _, card in scored]


def due_summary(conn: sqlite3.Connection, now: datetime | None = None) -> DueSummary:
    """Due-card counts for every deck, newest deck first."""
    at = now or datetime.now(UTC)
    decks: list[DeckDueSummary] = []
    total = 0
    for row in conn.execute("SELECT id, course, title FROM flashcard_decks ORDER BY id DESC"):
        count = len(due_cards(conn, row["id"], now=at))
        decks.append(
            DeckDueSummary(deck_id=row["id"], course=row["course"], title=row["title"], due=count)
        )
        total += count
    return DueSummary(total=total, decks=decks)


def grade_card(
    conn: sqlite3.Connection,
    deck_id: int,
    ref: str,
    name: str,
    now: datetime | None = None,
) -> GradeResult:
    """Apply an FSRS review to one card and persist the new state."""
    _card_row(conn, deck_id, ref)
    at = now or datetime.now(UTC)
    state = _state_of(_latest_reviews(conn, deck_id).get(ref))
    try:
        new_card = scheduler.review(state, name, at)
    except SchedulerError as exc:
        raise FlashcardsError(str(exc)) from exc

    conn.execute(
        "INSERT INTO flashcard_reviews"
        " (card_id, deck_id, grade, state, step, stability, difficulty, due_at, last_review_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ref,
            deck_id,
            name,
            int(new_card.state),
            new_card.step,
            new_card.stability,
            new_card.difficulty,
            new_card.due.isoformat(),
            new_card.last_review.isoformat() if new_card.last_review else None,
        ),
    )
    conn.commit()
    return scheduler.result_for(new_card, name, at, card_id=ref)


# --- match scores ----------------------------------------------------------


def record_match_score(conn: sqlite3.Connection, deck_id: int, elapsed_ms: int, pairs: int) -> int:
    """Record one finished Match round. Returns the deck's best time in ms."""
    _deck_row(conn, deck_id)
    if elapsed_ms <= 0 or pairs <= 0:
        raise FlashcardsError("a match score needs a positive time and pair count")
    conn.execute(
        "INSERT INTO flashcard_match_scores (deck_id, elapsed_ms, pairs) VALUES (?, ?, ?)",
        (deck_id, elapsed_ms, pairs),
    )
    conn.commit()
    best = best_match_score(conn, deck_id)
    # A row was just inserted, so there is always a best.
    return best if best is not None else elapsed_ms


def best_match_score(conn: sqlite3.Connection, deck_id: int) -> int | None:
    """The deck's fastest recorded round, or ``None`` if it has never been played."""
    row = conn.execute(
        "SELECT MIN(elapsed_ms) AS best FROM flashcard_match_scores WHERE deck_id = ?",
        (deck_id,),
    ).fetchone()
    return row["best"] if row and row["best"] is not None else None
