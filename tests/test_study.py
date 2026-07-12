"""Tests for the coursework engine (fake generator — no agent SDK)."""

import json
import sqlite3
from pathlib import Path

import pytest

from backend.db import connect, init_schema
from backend.study.grader import grade_attempt
from backend.study.practice_exam import build_exam, render_exam_md, render_key_md
from backend.study.syllabus import parse_syllabus

CORPUS = [
    {
        "text": "Plate tectonics: the lithosphere is divided into plates that move over the asthenosphere.",
        "meta": {"path": "15-Courses/ES101/materials/deck.pdf", "page": 3, "course": "ES101"},
    },
    {
        "text": "Divergent boundaries form mid-ocean ridges where new crust is created.",
        "meta": {"path": "15-Courses/ES101/materials/deck.pdf", "page": 7, "course": "ES101"},
    },
]

RAW_EXAM = json.dumps(
    {
        "title": "Plate Tectonics Practice",
        "questions": [
            {
                "q": "What layer do plates move over?",
                "type": "mcq",
                "options": ["Asthenosphere", "Inner core", "Crust", "Troposphere"],
                "answer": "Asthenosphere",
                "explanation": "Plates ride on the asthenosphere.",
                "citation": {
                    "path": "15-Courses/ES101/materials/deck.pdf",
                    "page": 3,
                    "quote": "plates that move over the asthenosphere",
                },
            },
            {
                "q": "What forms at divergent boundaries?",
                "type": "short",
                "answer": "Mid-ocean ridges",
                "explanation": "New crust is created there.",
                "citation": {
                    "path": "15-Courses/ES101/materials/deck.pdf",
                    "page": 7,
                    "quote": "Divergent boundaries form mid-ocean ridges",
                },
            },
            {
                "q": "HALLUCINATED: When did Wegener win the Nobel Prize?",
                "type": "short",
                "answer": "1930",
                "explanation": "Made up.",
                "citation": {
                    "path": "15-Courses/ES101/materials/deck.pdf",
                    "page": 3,
                    "quote": "Wegener won the Nobel Prize in 1930",
                },
            },
        ],
    }
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "friday.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_build_exam_drops_uncited_questions() -> None:
    exam, dropped = build_exam("ES101", RAW_EXAM, CORPUS)

    assert len(exam.questions) == 2, "cited questions must survive"
    assert dropped == 1, "I6 violation: hallucinated question kept"
    assert all("Wegener" not in q.q for q in exam.questions)


def test_build_exam_handles_code_fences_and_renders() -> None:
    fenced = f"```json\n{RAW_EXAM}\n```"
    exam, _ = build_exam("ES101", fenced, CORPUS)

    exam_md = render_exam_md(exam)
    key_md = render_key_md(exam)
    assert "What layer do plates move over?" in exam_md
    assert "Asthenosphere" in key_md
    assert "deck.pdf p.3" in key_md, "citations must render"
    assert "Asthenosphere" not in exam_md.split("A)")[0], "exam body must not leak answers early"


def test_grade_attempt_scores_and_writes_review_queue(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "ES101" / "study").mkdir(parents=True)

    exam, _ = build_exam("ES101", RAW_EXAM, CORPUS)
    conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES (?, ?, ?)",
        ("ES101", exam.title, exam.model_dump_json()),
    )
    conn.commit()
    exam_id = conn.execute("SELECT id FROM exams").fetchone()["id"]

    result = grade_attempt(conn, vault, exam_id, ["Asthenosphere", "wrong answer"])

    assert result.score == 1
    assert result.total == 2
    assert result.weak_topics, "missed question must produce a weak topic"

    queue = (vault / "15-Courses" / "ES101" / "study" / "review-queue.md").read_text(
        encoding="utf-8"
    )
    assert "divergent" in queue.lower() or "mid-ocean" in queue.lower() or "deck.pdf" in queue

    row = conn.execute("SELECT score, total FROM attempts").fetchone()
    assert (row["score"], row["total"]) == (1, 2)


def test_grade_attempt_accepts_mcq_letters(conn: sqlite3.Connection, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "ES101" / "study").mkdir(parents=True)
    exam, _ = build_exam("ES101", RAW_EXAM, CORPUS)
    conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES (?, ?, ?)",
        ("ES101", exam.title, exam.model_dump_json()),
    )
    conn.commit()

    result = grade_attempt(conn, vault, 1, ["A", "mid ocean ridges"])

    assert result.score == 2, "letter answers and fuzzy short answers must count"


def test_parse_syllabus_creates_suggestions_not_tasks(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    materials = vault / "15-Courses" / "ES101" / "materials"
    materials.mkdir(parents=True)
    syllabus = materials / "syllabus.md"
    syllabus.write_text(
        "# ES101 Syllabus\n\n"
        "Midterm exam: 2026-10-15 covering units 1-3.\n"
        "Problem set 2 due 2026-09-30.\n"
        "Grading: 40% exams, 60% homework.\n",
        encoding="utf-8",
    )
    before = {f.as_posix() for f in vault.rglob("*") if f.is_file()}

    created = parse_syllabus(conn, syllabus, "ES101")

    assert created >= 2
    rows = conn.execute("SELECT kind, payload_json, status FROM suggestions").fetchall()
    assert all(row["kind"] == "task" for row in rows)
    assert all(row["status"] == "pending" for row in rows)
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert any("2026-10-15" in payload["due"] for payload in payloads)

    after = {f.as_posix() for f in vault.rglob("*") if f.is_file()}
    assert before == after, "I1 violation: syllabus import wrote to the vault"
