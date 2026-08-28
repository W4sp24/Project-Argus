"""Study endpoints, mounted by ``backend.main.create_app``.

The generator (agent) and vault index are injected so tests run with fakes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.study.corpus import (
    CourseInfo,
    CourseSourceInfo,
    course_corpus,
    course_sources,
    courses,
)
from backend.features.study.deletes import delete_course, delete_exam
from backend.features.study.grader import AttemptResult, grade_attempt, load_exam
from backend.features.study.jobs import run_exam_job, run_guide_job
from backend.features.study.practice_exam import (
    Generator,
    StudyError,
    generate_practice_exam,
)
from backend.features.study.study_guide import generate_study_guide
from backend.vault.errors import raise_http
from backend.vault.writer import WriterError, WriterForbidden, save_ingest_file

logger = logging.getLogger("argus.study")

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


class GuideRequest(BaseModel):
    course: str
    scope: str = "everything so far"
    # A registry model name (§7). Omitted keeps the default backend, so every
    # existing client keeps working without sending this.
    model: str | None = None
    # Vault-relative files ticked in the Course Hub's SOURCES rail. Omitted
    # (None) means the whole course, which is what every existing client
    # sends; [] means the user unticked everything and is refused rather than
    # silently widened back to the course.
    sources: list[str] | None = None
    # Ask for a job instead of an answer. See `BACKGROUND_NOTE`.
    background: bool = False


class ExamRequest(BaseModel):
    course: str
    topics: str | None = None
    n: int = 10
    difficulty: str = "medium"
    model: str | None = None
    sources: list[str] | None = None
    background: bool = False


# Why generation has two response shapes rather than one.
#
# Generating a guide or an exam takes minutes, and it used to be a bare
# ``await`` held open inside the request: navigate away and the backend still
# wrote the file, but nothing ever told the UI where it went. ``background:
# true`` makes it a row in the shared job store instead — 202 with a
# ``job_id``, polled at ``GET /api/ingest/jobs/{job_id}``, with the written
# path on the single item's ``summary_path`` and (for an exam) the
# ``exam_id`` in the job's ``params``.
#
# The synchronous shape is the default and is untouched, because ``web/``
# still reads ``{"path": ...}`` and ``{"exam_id": ..., "path": ...}``
# straight out of the response body. A flag rather than a sibling route so
# the request validation — the sanitised course, the 422s about an empty or
# unindexed selection — has exactly one implementation for both.


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


class CourseDeleteSummary(BaseModel):
    """A truthful report of what a course delete actually removed."""

    course: str
    purged: bool
    exams_removed: int
    attempts_removed: int
    decks_removed: int
    reviews_removed: int


class ExamDeleteSummary(BaseModel):
    exam_id: int
    attempts_removed: int


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a deterministic one."""
    threading.Thread(target=run, daemon=True).start()


def build_study_router(
    settings: Settings,
    generator: Generator,
    index_factory: Any,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    """All /api/study routes. ``index_factory() -> VaultIndex-like``.

    ``job_runner`` is the same injection the ingest router takes, and for the
    same reason: a test that spawned the real daemon thread would be testing
    ``threading``.
    """
    router = APIRouter(prefix="/api/study")
    run_job = job_runner or _default_job_runner

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("/courses", response_model=list[CourseInfo])
    def list_courses() -> list[CourseInfo]:
        return courses(settings.vault_path, taxonomy=settings.taxonomy)

    @router.get("/courses/{code}/sources", response_model=list[CourseSourceInfo])
    def course_sources_route(code: str) -> list[CourseSourceInfo]:
        """Real files (materials/notes/study) — powers the Course Hub SOURCES
        rail, which ``GET /api/notes`` alone can't (markdown-only)."""
        safe_code = SAFE_NAME_RE.sub("", code)
        counts: dict[str, int] | None = None
        try:
            counts = index_factory().chunk_counts()
        except Exception as exc:
            # An index that is unavailable (no [rag] extras) or broken must
            # not take the rail down with it -- the files are still real and
            # still listable; they just report an unknown chunk count. Same
            # posture as /api/index/status.
            logger.warning("course sources: chunk counts unavailable: %s", exc)
        return course_sources(
            settings.vault_path, safe_code, taxonomy=settings.taxonomy, chunk_counts=counts
        )

    @router.delete("/courses/{code}", response_model=CourseDeleteSummary)
    def remove_course(code: str, purge: bool = False) -> CourseDeleteSummary:
        """Delete a course's exams/decks (and, with ``purge``, its vault folder).

        ``purge=False`` cleans up DB rows only, for a course whose folder the
        user already removed by hand in Obsidian.
        """
        conn = db()
        try:
            result = delete_course(
                conn, settings.vault_path, code, purge=purge, taxonomy=settings.taxonomy
            )
        except WriterError as exc:
            raise_http(exc)
        finally:
            conn.close()
        return CourseDeleteSummary(
            course=code,
            purged=result.folder_removed,
            exams_removed=result.exams_removed,
            attempts_removed=result.attempts_removed,
            decks_removed=result.decks_removed,
            reviews_removed=result.reviews_removed,
        )

    @router.post("/upload")
    async def upload(course: Annotated[str, Form()], file: UploadFile) -> dict[str, str]:
        """Save one file into a course's materials/.

        The Course Hub now ingests through ``POST /api/ingest/jobs`` instead
        (progress, a generated note, one snapshot per batch), so this is the
        legacy single-file path. It used to write with
        ``destination.write_bytes``: no ``guard_user_path``, so a crafted
        ``course`` escaped the vault or landed in a protected zone (I1), and
        no snapshot, so the write had no undo point (I2). It goes through
        ``save_ingest_file`` now, which is the one writer both invariants live
        in — and which dedupes a collision to ``name-2`` rather than silently
        overwriting a file the user still wanted.
        """
        code = SAFE_NAME_RE.sub("", course)
        if not (settings.vault_path / settings.taxonomy.course_dir(code)).is_dir():
            raise HTTPException(status_code=404, detail=f"no course folder {course}")
        try:
            rel_path = save_ingest_file(
                settings.vault_path,
                settings.taxonomy.course_materials(code),
                file.filename or "upload.bin",
                await file.read(),
                taxonomy=settings.taxonomy,
            )
        except WriterForbidden as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        index = index_factory()
        threading.Thread(  # indexing may load the embedding model — keep off the request
            target=lambda: index.upsert_file(settings.vault_path, rel_path), daemon=True
        ).start()
        return {"path": rel_path, "status": "saved, indexing in background"}

    def _corpus_for(course: str, sources: list[str] | None) -> list[dict[str, Any]]:
        """The chunks a generation should read, or the reason there are none.

        Worth its own error rather than reusing the generators' "no indexed
        material for course X — upload to materials/ first": with a selection
        in play that sentence is simply wrong, and it sends the user off to
        upload a file they already have.
        """
        corpus = course_corpus(index_factory(), course, sources)
        if corpus or sources is None:
            return corpus
        if not sources:
            raise HTTPException(
                status_code=422,
                detail="no sources are selected — tick at least one file in the SOURCES rail",
            )
        raise HTTPException(
            status_code=422,
            detail=f"none of the {len(sources)} selected source(s) are indexed for {course} "
            "— pick different ones, or reindex from System",
        )

    def _generator_for(model: str | None) -> Generator:
        """Bind the injected generator to a chosen model, if it accepts one.

        Mirrors the ``/ws/chat`` bridge: model-aware generators get the model,
        single-argument ones (every test fake) keep working untouched. The
        TypeError is raised when the coroutine is *created*, not awaited, so it
        can only mean a signature mismatch.
        """
        if not model:
            return generator

        async def run(prompt: str) -> str:
            try:
                call = generator(prompt, model=model)
            except TypeError:
                call = generator(prompt)
            return await call

        return run

    def _accept(kind: str, course: str, filename: str, params: dict[str, Any]) -> str:
        """Write the queued job row for one generation. Returns its id.

        No single-flight check: study generation shares no resource with an
        ingest or a reindex, and nothing stops two of these from running
        together -- see ``ingest.store.SLOT_GROUPS``.
        """
        conn = db()
        try:
            return store.create_job(
                conn,
                target=settings.taxonomy.course_study(course),
                filenames=[filename],
                kind=kind,
                params=params,
            )
        finally:
            conn.close()

    @router.post("/guide")
    async def guide(request: GuideRequest) -> Any:
        # Sanitised for the same reason `course_sources_route` sanitises, and
        # it was not: `course` reaches `tax.course_study(course)`, which builds
        # a filesystem path that is then mkdir'd and written to. Not reachable
        # today -- a course name that escapes the vault matches no chunk, so
        # the empty corpus 422s first -- but that is an unrelated guard
        # standing in front of a path built from unvalidated input.
        course = SAFE_NAME_RE.sub("", request.course)
        # Read here, not in the job, on purpose: "none of the selected sources
        # are indexed" is a 422 about *this request*, and answering it with a
        # 202 and a job that fails a minute later is strictly worse.
        corpus = _corpus_for(course, request.sources)
        if request.background:
            job_id = _accept(
                "guide",
                course,
                f"{course} study guide",
                {"course": course, "scope": request.scope, "model": request.model},
            )
            run_job(
                lambda: run_guide_job(
                    job_id,
                    settings=settings,
                    generator=_generator_for(request.model),
                    corpus=corpus,
                    course=course,
                    scope=request.scope,
                )
            )
            return JSONResponse(status_code=202, content={"job_id": job_id})
        try:
            path = await generate_study_guide(
                settings.vault_path,
                _generator_for(request.model),
                corpus,
                course,
                request.scope,
                taxonomy=settings.taxonomy,
            )
        except StudyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"path": path}

    @router.post("/exam")
    async def exam(request: ExamRequest) -> Any:
        course = SAFE_NAME_RE.sub("", request.course)
        corpus = _corpus_for(course, request.sources)
        if request.background:
            job_id = _accept(
                "exam",
                course,
                f"{course} practice exam",
                {
                    "course": course,
                    "topics": request.topics,
                    "n": request.n,
                    "difficulty": request.difficulty,
                    "model": request.model,
                },
            )
            run_job(
                lambda: run_exam_job(
                    job_id,
                    settings=settings,
                    generator=_generator_for(request.model),
                    corpus=corpus,
                    course=course,
                    topics=request.topics,
                    n=request.n,
                    difficulty=request.difficulty,
                )
            )
            return JSONResponse(status_code=202, content={"job_id": job_id})
        conn = db()
        try:
            exam_id, built, path = await generate_practice_exam(
                settings.vault_path,
                conn,
                _generator_for(request.model),
                corpus,
                course,
                request.topics,
                request.n,
                request.difficulty,
                taxonomy=settings.taxonomy,
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
            return grade_attempt(
                conn, settings.vault_path, exam_id, request.answers, taxonomy=settings.taxonomy
            )
        except StudyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()

    @router.delete("/exams/{exam_id}", response_model=ExamDeleteSummary)
    def remove_exam(exam_id: int) -> ExamDeleteSummary:
        conn = db()
        try:
            attempts_removed = delete_exam(conn, exam_id)
        except StudyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()
        return ExamDeleteSummary(exam_id=exam_id, attempts_removed=attempts_removed)

    return router
