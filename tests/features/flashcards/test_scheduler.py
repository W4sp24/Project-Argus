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
