"""FSRS, and what each grade would cost before you pick one.

Scheduling is delegated to ``fsrs`` (PyPI: the maintained reference
implementation of the Free Spaced Repetition Scheduler) rather than a
hand-rolled reimplementation of the published update rules.

The interesting addition here is :func:`preview`. ``fsrs.Scheduler`` is pure —
reviewing a card returns a new card and mutates nothing — so all four futures
can be computed from one state without committing any of them. That is what
turns the grade bar from four unlabelled verbs into a real choice, and
:func:`grade` derives its own label from the same helper so the promise on the
button cannot drift from what pressing it does. See :func:`_scheduler` for the
one configuration choice that makes that guarantee hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler, State
from pydantic import BaseModel

GRADE_TO_RATING: dict[str, Rating] = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}

#: The four grades, easiest-to-hardest interval order. The UI renders them in
#: this order, and `preview` returns exactly these keys.
GRADES: tuple[str, ...] = ("again", "hard", "good", "easy")


def _scheduler() -> Scheduler:
    """The one scheduler configuration this app uses.

    ``enable_fuzzing=False``, deliberately, and it is the whole reason the
    grade buttons can be trusted. FSRS fuzz applies a random few percent to
    longer intervals so that a large collection's reviews do not pile onto one
    day. With it on, ``preview`` and the commit that follows call the scheduler
    twice and get different answers -- a button reading "9d" that then
    schedules 8d. A test caught exactly that.

    The load-spreading fuzz buys is worth little here: this is a single-user
    app with course-sized decks, not a ten-thousand-card collection where one
    heavy day is a real problem. A grade button that tells the truth is worth
    more.
    """
    return Scheduler(enable_fuzzing=False)


class SchedulerError(RuntimeError):
    """Raised for a grade name FSRS has no rating for."""


class GradeResult(BaseModel):
    """FSRS state after grading one card."""

    card_id: str
    grade: str
    stability: float
    difficulty: float
    due_at: str
    state: str
    #: The same human interval `preview` promised for this grade.
    due_label: str


def parse_dt(value: str) -> datetime:
    """Parse a stored timestamp, assuming UTC when it carries no zone."""
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def humanise(due: datetime, now: datetime) -> str:
    """``10m``, ``4d``, ``3mo`` — the unit a learner actually reasons in."""
    seconds = max(0, int((due - now).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    days = seconds // 86400
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def card_from_state(state: dict[str, Any] | None) -> FsrsCard:
    """Rebuild an ``fsrs.Card`` from a stored review row, or start a new one."""
    if state is None:
        return FsrsCard()
    return FsrsCard(
        state=State(state["state"]),
        step=state["step"],
        stability=state["stability"],
        difficulty=state["difficulty"],
        due=parse_dt(state["due_at"]),
        last_review=parse_dt(state["last_review_at"]) if state["last_review_at"] else None,
    )


def review(state: dict[str, Any] | None, grade: str, now: datetime) -> FsrsCard:
    """The scheduler's answer for one grade. Writes nothing, mutates nothing.

    Public because the store needs the ``fsrs.Card`` itself — ``state`` as an
    int, ``step``, ``last_review`` — to persist a review row, while callers
    that only want to show the outcome use :func:`grade` or :func:`preview`.
    Both are built from this, so there is one scheduling call site.
    """
    if grade not in GRADE_TO_RATING:
        raise SchedulerError(f"invalid grade {grade!r} — expected again/hard/good/easy")
    new_card, _log = _scheduler().review_card(
        card_from_state(state), GRADE_TO_RATING[grade], review_datetime=now
    )
    return new_card


def preview(state: dict[str, Any] | None, now: datetime | None = None) -> dict[str, str]:
    """What each of the four grades would do, without doing any of them.

    Pure: nothing is written and the passed-in state is not mutated.
    """
    at = now or datetime.now(UTC)
    return {name: humanise(review(state, name, at).due, at) for name in GRADES}


def result_for(new_card: FsrsCard, name: str, now: datetime, *, card_id: str = "") -> GradeResult:
    """Describe an already-computed review for the wire."""
    return GradeResult(
        card_id=card_id,
        grade=name,
        stability=new_card.stability,
        difficulty=new_card.difficulty,
        due_at=new_card.due.isoformat(),
        state=State(new_card.state).name,
        # The same helper `preview` used, so the button's promise and what
        # pressing it does cannot disagree.
        due_label=humanise(new_card.due, now),
    )


def grade(
    state: dict[str, Any] | None,
    name: str,
    now: datetime | None = None,
    *,
    card_id: str = "",
) -> GradeResult:
    """Compute one review and describe it. Raises :class:`SchedulerError`.

    Convenience over ``review`` + ``result_for`` for callers with nothing to
    persist -- the tests, and any future read-only consumer.
    """
    at = now or datetime.now(UTC)
    return result_for(review(state, name, at), name, at, card_id=card_id)
