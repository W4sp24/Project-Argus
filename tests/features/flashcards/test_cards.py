"""Decks and cards as things you can author, not artefacts of one parse."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.flashcards import scheduler, store
from backend.features.flashcards.store import FlashcardsError


def _due_base(conn: sqlite3.Connection, deck_id: int) -> datetime:
    """A clock reading at which this deck's untouched cards are due.

    Read off the deck rather than written as a literal. `created_at` comes from
    SQLite's `datetime('now')`, and a card with no review yet is due *as of
    deck creation* -- so pinning `now` to a fixed timestamp silently asserts
    against wall-clock time. The literal here was written at 09:00 and started
    failing at 12:19 the same day, which is the worst way to learn this. The
    one second of slack absorbs `datetime('now')`'s second granularity.
    """
    row = conn.execute(
        "SELECT created_at FROM flashcard_decks WHERE id = ?", (deck_id,)
    ).fetchone()
    return scheduler.parse_dt(row["created_at"]) + timedelta(seconds=1)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    return connection


def _deck_with(conn: sqlite3.Connection, *fronts: str) -> int:
    deck_id = store.create_deck(conn, title="Deck")
    store.add_cards(conn, deck_id, [{"front": f, "back": f"answer to {f}"} for f in fronts])
    return deck_id


# --- decks -----------------------------------------------------------------


def test_a_deck_needs_no_course(conn: sqlite3.Connection) -> None:
    """The common case for a deck typed in by hand."""
    deck_id = store.create_deck(conn, title="Spanish verbs")
    deck = store.load_deck(conn, deck_id)
    assert deck.course == ""
    assert deck.source == "manual"
    assert deck.cards == 0


def test_a_deck_needs_a_title(conn: sqlite3.Connection) -> None:
    with pytest.raises(FlashcardsError, match="needs a title"):
        store.create_deck(conn, title="   ")


def test_a_deck_can_be_renamed_and_refiled(conn: sqlite3.Connection) -> None:
    deck_id = store.create_deck(conn, title="Untitled")
    updated = store.update_deck(conn, deck_id, title="Graph theory", course="CS301")
    assert (updated.title, updated.course) == ("Graph theory", "CS301")
    # An omitted field is untouched, not blanked.
    again = store.update_deck(conn, deck_id, description="for the midterm")
    assert (again.title, again.description) == ("Graph theory", "for the midterm")


def test_listing_reports_the_real_card_count(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b", "c")
    assert [deck.cards for deck in store.list_decks(conn) if deck.id == deck_id] == [3]


def test_deleting_a_deck_takes_its_cards_reviews_and_scores(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    store.grade_card(conn, deck_id, ref, "good", now)
    store.record_match_score(conn, deck_id, 12_000, 6)

    assert store.delete_deck(conn, deck_id) == 1
    for table in ("flashcard_cards", "flashcard_reviews", "flashcard_match_scores"):
        left = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE deck_id = ?", (deck_id,)
        ).fetchone()["n"]
        assert left == 0, f"{table} still holds rows for a deleted deck"


# --- cards -----------------------------------------------------------------


def test_cards_keep_the_order_they_were_added_in(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "first", "second", "third")
    assert [c.front for c in store.load_deck(conn, deck_id).card_list] == [
        "first",
        "second",
        "third",
    ]


def test_adding_appends_after_the_existing_cards(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "first")
    store.add_cards(conn, deck_id, [{"front": "second", "back": "b"}])
    assert [c.position for c in store.load_deck(conn, deck_id).card_list] == [0, 1]


def test_a_card_missing_a_face_is_not_added(conn: sqlite3.Connection) -> None:
    deck_id = store.create_deck(conn, title="Deck")
    added = store.add_cards(
        conn,
        deck_id,
        [
            {"front": "kept", "back": "yes"},
            {"front": "no answer", "back": "  "},
            {"front": "", "back": "no question"},
        ],
    )
    assert added == 1, "the count must be what the user actually got"


def test_a_card_can_be_edited_without_touching_its_neighbours(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    store.update_card(conn, deck_id, ref, back="rewritten", hint="starts with r")
    cards = store.load_deck(conn, deck_id).card_list
    assert (cards[0].back, cards[0].hint) == ("rewritten", "starts with r")
    assert cards[1].back == "answer to b"


def test_an_empty_hint_clears_it_but_an_empty_face_is_refused(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    store.update_card(conn, deck_id, ref, hint="x")
    assert store.update_card(conn, deck_id, ref, hint="").hint is None
    with pytest.raises(FlashcardsError, match="needs a front"):
        store.update_card(conn, deck_id, ref, front="   ")


def test_starring_persists(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    store.update_card(conn, deck_id, ref, starred=True)
    assert store.load_deck(conn, deck_id).card_list[0].starred is True


def test_editing_an_unknown_card_is_an_error(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    with pytest.raises(FlashcardsError, match="no card"):
        store.update_card(conn, deck_id, "cnope", front="x")


def test_deleting_a_card_takes_its_review_history(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    store.grade_card(conn, deck_id, ref, "good", now)
    assert store.delete_card(conn, deck_id, ref) == 1
    assert len(store.load_deck(conn, deck_id).card_list) == 1


def test_reorder_rewrites_positions(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b", "c")
    refs = [c.ref for c in store.load_deck(conn, deck_id).card_list]
    store.reorder_cards(conn, deck_id, [refs[2], refs[0], refs[1]])
    assert [c.front for c in store.load_deck(conn, deck_id).card_list] == ["c", "a", "b"]


def test_a_partial_order_is_refused_rather_than_applied(conn: sqlite3.Connection) -> None:
    """Leaving unnamed cards at stale positions produces an order nobody asked for."""
    deck_id = _deck_with(conn, "a", "b", "c")
    refs = [c.ref for c in store.load_deck(conn, deck_id).card_list]
    with pytest.raises(FlashcardsError, match="exactly once"):
        store.reorder_cards(conn, deck_id, [refs[0], refs[1]])
    with pytest.raises(FlashcardsError, match="exactly once"):
        store.reorder_cards(conn, deck_id, [refs[0], refs[0], refs[1], refs[2]])


# --- scheduling ------------------------------------------------------------


def test_a_new_card_is_due_immediately_and_carries_a_full_preview(
    conn: sqlite3.Connection,
) -> None:
    deck_id = _deck_with(conn, "a")
    now = _due_base(conn, deck_id)
    [due] = store.due_cards(conn, deck_id, now=now)
    assert due.state == "New"
    assert set(due.preview) == {"again", "hard", "good", "easy"}


def test_grading_removes_a_card_from_the_queue_until_it_is_due_again(
    conn: sqlite3.Connection,
) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    result = store.grade_card(conn, deck_id, ref, "easy", now)
    assert store.due_cards(conn, deck_id, now=now) == []
    # ...and comes back exactly when the grade said it would.
    due_at = scheduler.parse_dt(result.due_at)
    assert store.due_cards(conn, deck_id, now=due_at - timedelta(seconds=1)) == []
    assert [c.id for c in store.due_cards(conn, deck_id, now=due_at)] == [ref]


def test_a_suspended_card_is_never_due(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    store.update_card(conn, deck_id, ref, suspended=True)
    assert [c.id for c in store.due_cards(conn, deck_id, now=now)] != [ref]
    assert len(store.due_cards(conn, deck_id, now=now)) == 1


def test_the_latest_review_is_the_one_that_counts(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    store.grade_card(conn, deck_id, ref, "easy", now)
    store.grade_card(conn, deck_id, ref, "again", now + timedelta(minutes=1))
    # `again` sends it back to the front of the queue, so it is due again soon.
    assert store.due_cards(conn, deck_id, now=now + timedelta(hours=1)) != []


def test_grading_an_unknown_grade_is_an_error(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    now = _due_base(conn, deck_id)
    with pytest.raises(FlashcardsError, match="invalid grade"):
        store.grade_card(conn, deck_id, ref, "brilliant", now)


def test_due_summary_counts_every_deck(conn: sqlite3.Connection) -> None:
    _deck_with(conn, "a", "b")
    # The later deck: its creation is the last one every card has to be due by.
    second = _deck_with(conn, "c")
    now = _due_base(conn, second)
    summary = store.due_summary(conn, now=now)
    assert summary.total == 3
    assert sorted(deck.due for deck in summary.decks) == [1, 2]


# --- match -----------------------------------------------------------------


def test_match_keeps_the_fastest_round(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    assert store.best_match_score(conn, deck_id) is None
    store.record_match_score(conn, deck_id, 20_000, 6)
    assert store.record_match_score(conn, deck_id, 14_500, 6) == 14_500
    # A slower round does not replace the best.
    assert store.record_match_score(conn, deck_id, 31_000, 6) == 14_500


def test_match_scores_never_touch_the_schedule(conn: sqlite3.Connection) -> None:
    """Match is a game. Playing it must not spend a card's review."""
    deck_id = _deck_with(conn, "a", "b")
    now = _due_base(conn, deck_id)
    before = store.due_summary(conn, now=now).total
    store.record_match_score(conn, deck_id, 9_000, 2)
    assert store.due_summary(conn, now=now).total == before


def test_a_nonsense_match_score_is_refused(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a")
    with pytest.raises(FlashcardsError, match="positive"):
        store.record_match_score(conn, deck_id, 0, 6)


# --- mastery ---------------------------------------------------------------


def _reviewed(
    conn: sqlite3.Connection,
    deck_id: int,
    ref: str,
    *,
    interval_days: float,
    on: datetime | None = None,
) -> None:
    """Write one review row with a chosen interval.

    Direct insertion rather than `grade_card`, on purpose: the threshold is the
    thing under test, and driving FSRS to land exactly on it would be asserting
    against the scheduler's parameters instead of against our own rule.
    `test_grading_eventually_masters_a_card` covers the real path.
    """
    at = on or datetime.now(UTC)
    conn.execute(
        "INSERT INTO flashcard_reviews"
        " (card_id, deck_id, grade, state, step, stability, difficulty,"
        "  due_at, last_review_at, created_at)"
        " VALUES (?, ?, 'good', 2, NULL, 10.0, 5.0, ?, ?, ?)",
        (
            ref,
            deck_id,
            (at + timedelta(days=interval_days)).isoformat(),
            at.isoformat(),
            at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()


def test_a_card_is_mastered_at_three_weeks(conn: sqlite3.Connection) -> None:
    """The threshold is the rule, so pin both sides of it."""
    deck_id = _deck_with(conn, "just short", "just over")
    short, over = (card.ref for card in store.load_deck(conn, deck_id).card_list)
    _reviewed(conn, deck_id, short, interval_days=store.MASTERED_INTERVAL_DAYS - 0.01)
    _reviewed(conn, deck_id, over, interval_days=store.MASTERED_INTERVAL_DAYS)

    assert store.load_deck(conn, deck_id).mastered == 1


def test_a_new_card_is_not_mastered(conn: sqlite3.Connection) -> None:
    """New is not mastered — and a deck nobody has studied reads as zero."""
    deck_id = _deck_with(conn, "a", "b")
    deck = store.load_deck(conn, deck_id)
    assert deck.mastered == 0
    assert deck.cards == 2


def test_mastery_reaches_the_library_listing(conn: sqlite3.Connection) -> None:
    """`list_decks` counts every deck in one pass; prove it keeps them apart."""
    first = _deck_with(conn, "a", "b")
    second = _deck_with(conn, "c")
    for card in store.load_deck(conn, first).card_list:
        _reviewed(conn, first, card.ref, interval_days=40)

    by_id = {deck.id: deck for deck in store.list_decks(conn)}
    assert by_id[first].mastered == 2
    assert by_id[second].mastered == 0


def test_mastery_follows_the_latest_review_not_the_best_one(conn: sqlite3.Connection) -> None:
    """A card you have forgotten stops being mastered."""
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    _reviewed(conn, deck_id, ref, interval_days=40)
    assert store.load_deck(conn, deck_id).mastered == 1
    _reviewed(conn, deck_id, ref, interval_days=0.007)  # ~10 minutes: back to square one
    assert store.load_deck(conn, deck_id).mastered == 0


def test_deleting_a_card_takes_its_mastery_with_it(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b")
    refs = [card.ref for card in store.load_deck(conn, deck_id).card_list]
    for ref in refs:
        _reviewed(conn, deck_id, ref, interval_days=40)
    store.delete_card(conn, deck_id, refs[0])
    assert store.load_deck(conn, deck_id).mastered == 1


def test_grading_eventually_masters_a_card(conn: sqlite3.Connection) -> None:
    """The real path: repeated `easy` grades push a card past three weeks."""
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    at = _due_base(conn, deck_id)
    for _ in range(6):
        result = store.grade_card(conn, deck_id, ref, "easy", now=at)
        at = scheduler.parse_dt(result.due_at)
    assert store.load_deck(conn, deck_id).mastered == 1


# --- a card's next review --------------------------------------------------


def test_an_unreviewed_card_has_no_next_review(conn: sqlite3.Connection) -> None:
    """`None` rather than the deck's creation time: the editor renders "Due now"
    for a new card, and inventing a timestamp would make that a lie it has to
    decode."""
    deck_id = _deck_with(conn, "a")
    card = store.load_deck(conn, deck_id).card_list[0]
    assert card.due_at is None
    assert card.state is None


def test_a_reviewed_card_carries_its_next_review(conn: sqlite3.Connection) -> None:
    """`due_cards` answers this for the due subset; the editor needs every card,
    including the ones scheduled weeks out."""
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    result = store.grade_card(conn, deck_id, ref, "good", now=_due_base(conn, deck_id))

    card = store.load_deck(conn, deck_id).card_list[0]
    assert card.due_at == result.due_at
    assert card.state == result.state
    # And it survives an edit, which returns a CardInfo of its own.
    edited = store.update_card(conn, deck_id, ref, starred=True)
    assert edited.due_at == result.due_at


# --- mastered per day ------------------------------------------------------


def test_mastered_history_covers_every_day_in_the_window(conn: sqlite3.Connection) -> None:
    """Empty days are present. A chart with days missing reads as a shorter
    history rather than a quieter one."""
    deck_id = _deck_with(conn, "a")
    history = store.mastered_history(conn, days=7)
    assert len(history) == 7
    assert [day.mastered for day in history] == [0] * 7
    # Ascending, ending today.
    assert [day.date for day in history] == sorted(day.date for day in history)
    assert history[-1].date == datetime.now(UTC).date().isoformat()
    assert deck_id  # the deck exists; nothing has been studied in it


def test_mastered_history_counts_the_day_the_card_crossed(conn: sqlite3.Connection) -> None:
    deck_id = _deck_with(conn, "a", "b")
    refs = [card.ref for card in store.load_deck(conn, deck_id).card_list]
    now = datetime.now(UTC)
    _reviewed(conn, deck_id, refs[0], interval_days=40, on=now - timedelta(days=2))
    # Same day, but a ten-minute interval is not mastery.
    _reviewed(conn, deck_id, refs[1], interval_days=0.007, on=now - timedelta(days=2))

    history = {day.date: day.mastered for day in store.mastered_history(conn, days=7)}
    assert history[(now - timedelta(days=2)).date().isoformat()] == 1
    assert sum(history.values()) == 1


def test_mastered_history_ignores_what_falls_out_of_the_window(
    conn: sqlite3.Connection,
) -> None:
    deck_id = _deck_with(conn, "a")
    ref = store.load_deck(conn, deck_id).card_list[0].ref
    _reviewed(conn, deck_id, ref, interval_days=40, on=datetime.now(UTC) - timedelta(days=30))
    assert sum(day.mastered for day in store.mastered_history(conn, days=7)) == 0
    assert sum(day.mastered for day in store.mastered_history(conn, days=60)) == 1
