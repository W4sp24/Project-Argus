"""Grade quiz attempts and feed weak topics into the review queue."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.study.practice_exam import Exam, Question, StudyError


class QuestionFeedback(BaseModel):
    """Per-question grading detail shown after the quiz."""

    q: str
    your_answer: str
    correct_answer: str
    correct: bool
    explanation: str
    citation: str


class AttemptResult(BaseModel):
    """Outcome of one graded attempt."""

    attempt_id: int
    exam_id: int
    score: int
    total: int
    feedback: list[QuestionFeedback]
    weak_topics: list[str]


#: LaTeX commands that carry no word a student would ever type -- they place
#: their arguments rather than name anything. Deleted before comparison.
#:
#: Deliberately *only* layout. An operator keeps its name, because ``\log`` is
#: something a person writes: strip it and ``$O(\log n)$`` normalises to
#: ``"o n"``, which would then accept a bare ``O(n)`` -- a different complexity
#: class, graded correct.
_LATEX_LAYOUT = frozenset(
    {
        "frac",
        "dfrac",
        "tfrac",
        "left",
        "right",
        "begin",
        "end",
        "text",
        "textbf",
        "textit",
        "textrm",
        "mathrm",
        "mathbf",
        "mathit",
        "displaystyle",
        "quad",
        "qquad",
    }
)

_LATEX_COMMAND = re.compile(r"\\([a-zA-Z]+)")

#: ``\begin{cases}`` and its closer, taken with the environment name. That name
#: is an *argument*, so the command rule above cannot reach it -- and left
#: behind it becomes the word "cases", which containment then accepts.
_LATEX_ENVIRONMENT = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}")


def _strip_latex(text: str) -> str:
    r"""Drop the markup, keep the content.

    ``_normalize`` below reduces everything to ``[a-z0-9]``, which strips a
    command's backslash and leaves the command *as a word*: ``\frac{1}{2}``
    becomes ``"frac 1 2"``. Since a short answer is graded by containment
    either way, that word is not merely noise -- ``frac`` becomes an accepted
    answer, and the student who types it scores the point.
    """
    return _LATEX_COMMAND.sub(
        lambda match: " " if match.group(1) in _LATEX_LAYOUT else f" {match.group(1)} ",
        _LATEX_ENVIRONMENT.sub(" ", text),
    )


def _normalize(text: str) -> str:
    """Comparable form of an answer.

    Distinct from ``practice_exam._normalize``, which is *not* LaTeX-aware and
    should not be: that one checks a model's quote against the source text it
    was copied from, so both sides carry the same markup. This one compares a
    model's answer against what a human typed into an ``<input>``, and only one
    side of that has ever seen a backslash.
    """
    return re.sub(r"[^a-z0-9]+", " ", _strip_latex(text).lower()).strip()


def _is_correct(question: Question, answer: str) -> bool:
    given = _normalize(answer)
    if not given:
        return False
    expected = _normalize(question.answer)
    if question.type == "mcq" and question.options:
        # Accept a bare option letter (A-H) or the option text itself.
        letters = {
            letter.lower(): _normalize(option)
            for letter, option in zip("ABCDEFGH", question.options, strict=False)
        }
        if given in letters:
            given = letters[given]
        return given == expected
    # Short/problem answers: exact after normalization, or containment either way.
    return given == expected or expected in given or given in expected


def _weak_topic(question: Question) -> str:
    return question.citation.label()


def load_exam(conn: sqlite3.Connection, exam_id: int) -> Exam:
    row = conn.execute("SELECT questions_json FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if row is None:
        raise StudyError(f"no exam with id {exam_id}")
    return Exam.model_validate_json(row["questions_json"])


def grade_attempt(
    conn: sqlite3.Connection,
    vault_path: Path,
    exam_id: int,
    answers: list[str],
    *,
    taxonomy: Taxonomy | None = None,
) -> AttemptResult:
    """Score an attempt, persist it, and append weak topics to review-queue.md."""
    exam = load_exam(conn, exam_id)

    feedback: list[QuestionFeedback] = []
    weak_topics: list[str] = []
    score = 0
    for question, answer in zip(exam.questions, answers + [""] * len(exam.questions), strict=False):
        correct = _is_correct(question, answer)
        score += int(correct)
        if not correct:
            topic = _weak_topic(question)
            if topic not in weak_topics:
                weak_topics.append(topic)
        feedback.append(
            QuestionFeedback(
                q=question.q,
                your_answer=answer,
                correct_answer=question.answer,
                correct=correct,
                explanation=question.explanation,
                citation=question.citation.label(),
            )
        )

    cursor = conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, weak_topics)"
        " VALUES (?, ?, ?, ?, ?)",
        (exam_id, score, len(exam.questions), json.dumps(answers), ", ".join(weak_topics)),
    )
    conn.commit()

    if weak_topics:
        _append_review_queue(vault_path, exam, weak_topics, score, taxonomy=taxonomy)

    return AttemptResult(
        attempt_id=int(cursor.lastrowid),
        exam_id=exam_id,
        score=score,
        total=len(exam.questions),
        feedback=feedback,
        weak_topics=weak_topics,
    )


def _append_review_queue(
    vault_path: Path,
    exam: Exam,
    weak_topics: list[str],
    score: int,
    *,
    taxonomy: Taxonomy | None = None,
) -> None:
    """Append weak topics under study/ (allowed write target, I1 exemption)."""
    tax = taxonomy or active_taxonomy()
    study_dir = vault_path / tax.course_study(exam.course)
    study_dir.mkdir(parents=True, exist_ok=True)
    queue = study_dir / "review-queue.md"
    if not queue.exists():
        queue.write_text(
            f"# {exam.course} — review queue\n\n"
            "Topics Argus thinks you should revisit, from missed exam questions.\n",
            encoding="utf-8",
        )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {stamp} — {exam.title} ({score}/{len(exam.questions)})\n"]
    lines += [f"- [ ] Review: {topic}\n" for topic in weak_topics]
    with queue.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)
