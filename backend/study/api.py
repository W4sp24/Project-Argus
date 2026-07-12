"""Study endpoints, mounted by ``backend.main.create_app``.

The generator (agent) and vault index are injected so tests run with fakes.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.config import Settings
from backend.db import connect, init_schema
from backend.study.corpus import CourseInfo, course_corpus, courses
from backend.study.grader import AttemptResult, grade_attempt, load_exam
from backend.study.practice_exam import (
    Generator,
    StudyError,
    generate_practice_exam,
)
from backend.study.study_guide import generate_study_guide

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


class GuideRequest(BaseModel):
    course: str
    scope: str = "everything so far"


class ExamRequest(BaseModel):
    course: str
    topics: str | None = None
    n: int = 10
    difficulty: str = "medium"


class ExamSummary(BaseModel):
    id: int
    course: str
    title: str
    created_at: str
    questions: int


class QuizQuestion(BaseModel):
    """A question as shown during the quiz — no answer, no explanation."""

    q: str
    type: str
    options: list[str] | None = None


class AttemptRequest(BaseModel):
    answers: list[str]


def build_study_router(
    settings: Settings,
    generator: Generator,
    index_factory: Any,
) -> APIRouter:
    """All /api/study routes. ``index_factory() -> VaultIndex-like``."""
    router = APIRouter(prefix="/api/study")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("/courses", response_model=list[CourseInfo])
    def list_courses() -> list[CourseInfo]:
        return courses(settings.vault_path)

    @router.post("/upload")
    async def upload(
        course: Annotated[str, Form()], file: UploadFile
    ) -> dict[str, str]:
        course_dir = settings.vault_path / "15-Courses" / SAFE_NAME_RE.sub("", course)
        if not course_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"no course folder {course}")
        name = SAFE_NAME_RE.sub("_", file.filename or "upload.bin")
        materials = course_dir / "materials"
        materials.mkdir(exist_ok=True)
        destination = materials / name
        destination.write_bytes(await file.read())

        rel_path = destination.relative_to(settings.vault_path).as_posix()
        index = index_factory()
        threading.Thread(  # indexing may load the embedding model — keep off the request
            target=lambda: index.upsert_file(settings.vault_path, rel_path), daemon=True
        ).start()
        return {"path": rel_path, "status": "saved, indexing in background"}

    @router.post("/guide")
    async def guide(request: GuideRequest) -> dict[str, str]:
        corpus = course_corpus(index_factory(), request.course)
        try:
            path = await generate_study_guide(
                settings.vault_path, generator, corpus, request.course, request.scope
            )
        except StudyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"path": path}

    @router.post("/exam")
    async def exam(request: ExamRequest) -> dict[str, Any]:
        corpus = course_corpus(index_factory(), request.course)
        conn = db()
        try:
            exam_id, built, path = await generate_practice_exam(
                settings.vault_path,
                conn,
                generator,
                corpus,
                request.course,
                request.topics,
                request.n,
                request.difficulty,
            )
        except StudyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()
        return {"exam_id": exam_id, "path": path, "questions": len(built.questions)}

    @router.get("/exams", response_model=list[ExamSummary])
    def list_exams(course: str | None = None) -> list[ExamSummary]:
        conn = db()
        try:
            rows = conn.execute(
                "SELECT e.id, e.course, e.title, e.created_at, e.questions_json FROM exams e"
                + (" WHERE course = ?" if course else "")
                + " ORDER BY e.id DESC",
                (course,) if course else (),
            ).fetchall()
        finally:
            conn.close()
        summaries = []
        for row in rows:
            import json

            count = len(json.loads(row["questions_json"]).get("questions", []))
            summaries.append(
                ExamSummary(
                    id=row["id"],
                    course=row["course"],
                    title=row["title"],
                    created_at=row["created_at"],
                    questions=count,
                )
            )
        return summaries

    @router.get("/exams/{exam_id}", response_model=list[QuizQuestion])
    def quiz_questions(exam_id: int) -> list[QuizQuestion]:
        conn = db()
        try:
            exam = load_exam(conn, exam_id)
        except StudyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()
        return [
            QuizQuestion(q=question.q, type=question.type, options=question.options)
            for question in exam.questions
        ]

    @router.post("/exams/{exam_id}/attempt", response_model=AttemptResult)
    def attempt(exam_id: int, request: AttemptRequest) -> AttemptResult:
        conn = db()
        try:
            return grade_attempt(conn, settings.vault_path, exam_id, request.answers)
        except StudyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()

    return router
