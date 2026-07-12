"""Grade quiz attempts and feed weak topics into the review queue."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from backend.study.practice_exam import Exam, Question, StudyError


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


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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
    conn: sqlite3.Connection, vault_path: Path, exam_id: int, answers: list[str]
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
        _append_review_queue(vault_path, exam, weak_topics, score)

    return AttemptResult(
        attempt_id=int(cursor.lastrowid),
        exam_id=exam_id,
        score=score,
        total=len(exam.questions),
        feedback=feedback,
        weak_topics=weak_topics,
    )


def _append_review_queue(vault_path: Path, exam: Exam, weak_topics: list[str], score: int) -> None:
    """Append weak topics under study/ (allowed write target, I1 exemption)."""
    study_dir = vault_path / "15-Courses" / exam.course / "study"
    study_dir.mkdir(parents=True, exist_ok=True)
    queue = study_dir / "review-queue.md"
    if not queue.exists():
        queue.write_text(
            f"# {exam.course} — review queue\n\n"
            "Topics FRIDAY thinks you should revisit, from missed exam questions.\n",
            encoding="utf-8",
        )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {stamp} — {exam.title} ({score}/{len(exam.questions)})\n"]
    lines += [f"- [ ] Review: {topic}\n" for topic in weak_topics]
    with queue.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)
