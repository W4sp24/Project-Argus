"""Practice exams grounded in real course materials.

Every question must cite a verbatim quote from the course corpus; questions
whose citation cannot be verified are dropped (invariant I6). Exam files are
NEW files under ``15-Courses/<C>/study/`` — the one direct-write exemption to
the single-writer rule (I1).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Generator = Callable[[str], Awaitable[str]]

MAX_PROMPT_CHARS = 60_000


class StudyError(RuntimeError):
    """Raised when study content cannot be generated."""


class Citation(BaseModel):
    """Verbatim pointer into the course corpus."""

    path: str
    page: int | None = None
    slide: int | None = None
    quote: str

    def label(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        if self.page is not None:
            return f"{name} p.{self.page}"
        if self.slide is not None:
            return f"{name} slide {self.slide}"
        return name


class Question(BaseModel):
    """One validated exam question."""

    q: str
    type: Literal["mcq", "short", "problem"]
    options: list[str] | None = None
    answer: str
    explanation: str = ""
    citation: Citation


class Exam(BaseModel):
    """A validated, citable practice exam."""

    course: str
    title: str
    questions: list[Question] = Field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _strip_fences(raw: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1) if match else raw


def _citation_verified(citation: Citation, corpus: list[dict[str, Any]]) -> bool:
    quote = _normalize(citation.quote)
    if not quote:
        return False
    for chunk in corpus:
        if chunk["meta"].get("path") != citation.path:
            continue
        if quote in _normalize(chunk["text"]):
            return True
    return False


def build_exam(course: str, raw: str, corpus: list[dict[str, Any]]) -> tuple[Exam, int]:
    """Parse generator output; keep only verifiably-cited questions.

    Returns the exam and the number of dropped questions.
    """
    try:
        payload = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise StudyError(f"generator returned invalid JSON: {exc}") from exc

    kept: list[Question] = []
    dropped = 0
    for raw_question in payload.get("questions", []):
        try:
            question = Question.model_validate(raw_question)
        except Exception:
            dropped += 1
            continue
        if _citation_verified(question.citation, corpus):
            kept.append(question)
        else:
            dropped += 1  # I6: uncited questions never ship

    return Exam(
        course=course, title=str(payload.get("title") or f"{course} practice exam"), questions=kept
    ), dropped


def render_exam_md(exam: Exam) -> str:
    lines = [f"# {exam.title}", "", f"Course: {exam.course} · {len(exam.questions)} questions", ""]
    for number, question in enumerate(exam.questions, start=1):
        lines += [f"## {number}. {question.q}", ""]
        if question.type == "mcq" and question.options:
            for letter, option in zip("ABCDEFGH", question.options, strict=False):
                lines.append(f"- {letter}) {option}")
            lines.append("")
    lines.append("> Answers with citations are in the matching `-key.md` file.")
    return "\n".join(lines)


def render_key_md(exam: Exam) -> str:
    lines = [f"# {exam.title} — answer key", ""]
    for number, question in enumerate(exam.questions, start=1):
        lines += [
            f"## {number}. {question.q}",
            "",
            f"**Answer:** {question.answer}",
            f"**Why:** {question.explanation}",
            f"**Source:** {question.citation.label()} — “{question.citation.quote}”",
            "",
        ]
    return "\n".join(lines)


def exam_prompt(
    course: str, corpus: list[dict[str, Any]], topics: str | None, n: int, difficulty: str
) -> str:
    excerpts: list[str] = []
    used = 0
    for chunk in corpus:
        meta = chunk["meta"]
        where = (
            f"page {meta['page']}"
            if meta.get("page")
            else (f"slide {meta['slide']}" if meta.get("slide") else "note")
        )
        block = f"[SOURCE path={meta.get('path')} {where}]\n{chunk['text']}\n"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        excerpts.append(block)
        used += len(block)

    topic_line = f"Focus on: {topics}." if topics else "Cover the material broadly."
    return f"""Create a {difficulty} practice exam with exactly {n} questions for course {course},
grounded ONLY in the source excerpts below. {topic_line}

Return ONLY JSON (no prose) with this exact schema:
{{"title": str, "questions": [{{"q": str, "type": "mcq"|"short"|"problem",
"options": [str, ...] (mcq only, 4 options), "answer": str, "explanation": str,
"citation": {{"path": str, "page": int|null, "slide": int|null, "quote": str}}}}]}}

Citation rules (questions violating them will be discarded):
- "path" must be one of the SOURCE paths verbatim.
- "quote" must be a short VERBATIM substring copied from that source excerpt.
- Do not ask about anything not present in the excerpts.

SOURCES:
{"".join(excerpts)}"""


async def generate_practice_exam(
    vault_path: Path,
    conn: sqlite3.Connection,
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    topics: str | None = None,
    n: int = 10,
    difficulty: str = "medium",
) -> tuple[int, Exam, str]:
    """Generate, validate, persist, and write one practice exam.

    Returns (exam_id, exam, vault-relative exam path).
    """
    if not corpus:
        raise StudyError(f"no indexed material for course {course} — upload to materials/ first")

    from backend.audit import log_prompt_conn

    log_prompt_conn(
        conn,
        "study",
        "claude-opus-4-8",
        [str(chunk["meta"].get("path")) for chunk in corpus if chunk["meta"].get("path")],
    )
    raw = await generator(exam_prompt(course, corpus, topics, n, difficulty))
    exam, dropped = build_exam(course, raw, corpus)
    if not exam.questions:
        raise StudyError(f"all {dropped} generated questions failed citation checks")

    study_dir = vault_path / "15-Courses" / course / "study"
    study_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    base = f"exam-{stamp}-{len(exam.questions)}q"
    suffix = 0
    while (study_dir / f"{base}{'-' + str(suffix) if suffix else ''}.md").exists():
        suffix += 1
    base = f"{base}{'-' + str(suffix) if suffix else ''}"
    (study_dir / f"{base}.md").write_text(render_exam_md(exam), encoding="utf-8")
    (study_dir / f"{base}-key.md").write_text(render_key_md(exam), encoding="utf-8")

    cursor = conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES (?, ?, ?)",
        (course, exam.title, exam.model_dump_json()),
    )
    conn.commit()
    return int(cursor.lastrowid), exam, f"15-Courses/{course}/study/{base}.md"
