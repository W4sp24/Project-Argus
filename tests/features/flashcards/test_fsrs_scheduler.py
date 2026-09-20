"""The grade bar's promise, and that pressing a button honours it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.features.flashcards.scheduler import (
    GRADES,
    SchedulerError,
    grade,
    humanise,
    preview,
    review,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def test_preview_offers_exactly_the_four_grades() -> None:
    assert set(preview(None, NOW)) == set(GRADES)


def test_preview_intervals_increase_with_the_grade() -> None:
    """`again` must never schedule further out than `easy`, or the bar lies."""
    dues = [review(None, name, NOW).due for name in GRADES]
    assert dues == sorted(dues), dict(zip(GRADES, dues, strict=True))


def test_preview_matches_what_committing_actually_does() -> None:
    # The preview is the promise printed on the button. If it can drift from
    # the commit, the button lies -- so both go through one code path.
    for name in GRADES:
        promised = preview(None, NOW)[name]
        assert grade(None, name, NOW).due_label == promised


def test_preview_writes_nothing_into_the_state_it_is_given() -> None:
    state = {
        "state": 2,
        "step": 0,
        "stability": 12.5,
        "difficulty": 4.75,
        "due_at": "2026-09-01T12:00:00+00:00",
        "last_review_at": "2026-08-20T12:00:00+00:00",
    }
    before = dict(state)
    preview(state, NOW)
    assert state == before


def test_a_reviewed_card_carries_forward_its_history() -> None:
    """A mature card must not be scheduled as if it were new."""
    mature = {
        "state": 2,
        "step": 0,
        "stability": 60.0,
        "difficulty": 4.0,
        "due_at": "2026-09-03T12:00:00+00:00",
        "last_review_at": "2026-07-05T12:00:00+00:00",
    }
    assert review(mature, "good", NOW).due > review(None, "good", NOW).due


def test_grade_rejects_an_unknown_name() -> None:
    with pytest.raises(SchedulerError, match="invalid grade"):
        grade(None, "brilliant", NOW)


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "1m"),  # never "0m" -- it reads as "not scheduled"
        (timedelta(minutes=10), "10m"),
        (timedelta(hours=5), "5h"),
        (timedelta(days=4), "4d"),
        (timedelta(days=95), "3mo"),
        (timedelta(days=800), "2y"),
        (timedelta(days=-1), "1m"),  # already overdue, never negative
    ],
)
def test_humanise_uses_the_unit_a_learner_reasons_in(delta: timedelta, expected: str) -> None:
    assert humanise(NOW + delta, NOW) == expected


def test_two_concurrent_grades_chain_rather_than_one_overwriting_the_other(tmp_path) -> None:
    """The silent loss this transaction exists to stop.

    FSRS state is derived from the newest review row, so two grades landing
    together both read the same prior state and the second becomes "latest" --
    discarding the first review's effect on stability entirely. The card is
    then scheduled as though it had been seen once instead of twice.

    Reachable from the UI: Learn's four choice buttons are all live while a
    grade is in flight.
    """
    import threading

    from backend.core.db import connect, init_schema
    from backend.features.flashcards import store

    db_path = tmp_path / "race.db"
    setup = connect(db_path)
    init_schema(setup)
    deck_id = store.create_deck(setup, course="CS201", title="Race")
    store.add_cards(setup, deck_id, [{"front": "q", "back": "a"}])
    ref = setup.execute(
        "SELECT card_ref FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
    ).fetchone()["card_ref"]
    setup.close()

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def grade() -> None:
        conn = connect(db_path)
        try:
            init_schema(conn)
            barrier.wait(timeout=10)
            store.grade_card(conn, deck_id, ref, "good")
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=grade) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, f"a grade failed under contention: {errors}"

    check = connect(db_path)
    try:
        rows = check.execute(
            "SELECT state, step, due_at FROM flashcard_reviews WHERE card_id = ? ORDER BY id",
            (ref,),
        ).fetchall()
    finally:
        check.close()

    assert len(rows) == 2, "both reviews must be recorded"
    # The second review scheduled *from the first* rather than from nothing.
    # A new card graded "good" enters learning (state 1, step 1, due in
    # minutes); graded "good" again it graduates (state 2, due in days). Two
    # reviews that both read "new" would both be state 1 and identical.
    #
    # Asserted on state rather than stability because both grades land at the
    # same instant, so zero time has elapsed and FSRS leaves stability alone.
    assert rows[0]["state"] == 1, "the first review put the card into learning"
    assert rows[1]["state"] == 2, "the second review did not see the first"
    assert rows[1]["due_at"] > rows[0]["due_at"]
