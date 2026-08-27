"""Ingestion endpoints (redesign §11): file upload + manual email capture.

Files land in the vault through :mod:`backend.vault.writer` (snapshot-first, I1)
and are then indexed with the existing extract → chunk → embed pipeline.
Email capture is manual by design — text is pasted or an ``.eml`` dropped;
there is deliberately NO IMAP/Gmail sync here. Extractions become proposals
in the review queue (mirroring the planner), never direct writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import notes as note_styles
from backend.features.ingest import store
from backend.features.ingest.pipeline import run_ingest_job
from backend.rag.email import parse_email
from backend.vault import suggestions as queue
from backend.vault.sources import SourceInfo, list_sources
from backend.vault.writer import (
    SAFE_NAME_RE,
    WriterError,
    WriterForbidden,
    archive_email,
    guard_user_path,
    save_ingest_file,
)

logger = logging.getLogger("argus.rag")

Generator = Callable[[str], Awaitable[str]]

ALLOWED_SUFFIXES = {".pdf", ".pptx", ".docx", ".md", ".eml"}

#: Batch caps. Starlette enforces neither: its `max_part_size` applies only to
#: non-file parts, so without these a single 900MB PDF is accepted, read into
#: memory, and then committed into the vault's git history as a 900MB blob.
MAX_BATCH_FILES = 50
MAX_FILE_BYTES = 100 * 1024 * 1024
#: Copied in chunks rather than via `await file.read()` so peak memory is one
#: chunk, not the whole batch.
COPY_CHUNK_BYTES = 1024 * 1024
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
BULLET_RE = re.compile(r"^\s*(?:[-*]\s+(?:\[ \]\s+)?|\d+[.)]\s+)(.+)$")

EXTRACT_PROMPT = """You extract actionable structure from one email.
Return ONLY a JSON object, no prose, of the shape:
{{"tasks": ["..."], "dates": ["YYYY-MM-DD ..."], "contacts": ["Name <addr>"], "summary": "..."}}
Tasks are concrete action items for the reader. Keep every date exactly as
written. summary is one sentence.

EMAIL:
{email}
"""



class SourcesResponse(BaseModel):
    """``GET /api/sources``.

    ``index_available`` exists because ``chunks`` is deliberately ``None`` for
    two different reasons -- "the index holds nothing for this file" and "there
    is no index to ask". Without this flag the UI cannot tell a vault with no
    [rag] extras from one that simply has not been indexed yet.
    """

    sources: list[SourceInfo]
    index_available: bool


class DestinationsResponse(BaseModel):
    """``GET /api/ingest/destinations`` — where an ingest may be pointed.

    Built from :class:`~backend.core.taxonomy.Taxonomy`, never concatenated in
    the frontend: a literal ``15-Courses/<CODE>/materials`` in the UI is the
    exact bug the configurable-taxonomy refactor fixed.
    """

    destinations: list[str]


class NoteStyleInfo(BaseModel):
    """One entry of ``GET /api/ingest/note-styles``.

    Served rather than hardcoded in the dialog for the same reason
    ``/api/ingest/destinations`` is: a list the frontend keeps its own copy of
    is a list that drifts the moment a style is added on this side.
    """

    key: str
    label: str
    description: str


class NoteStylesResponse(BaseModel):
    styles: list[NoteStyleInfo]


class PrecheckRequest(BaseModel):
    filename: str
    target: str


class PrecheckResponse(BaseModel):
    """What is already at ``<target>/<filename>``, so the UI can offer Replace.

    The hash is of the file **in the vault**; the browser hashes its own pick
    with ``crypto.subtle`` and compares. Same bytes means the user is
    re-adding something already ingested; different bytes means they have a
    newer version and deduping to ``name-2`` would leave the stale copy
    indexed beside it.
    """

    exists: bool
    path: str | None = None
    sha256: str | None = None


class JobAccepted(BaseModel):
    job_id: str


class IngestResponse(BaseModel):
    """POST /api/ingest result: where the file landed and how indexing went."""

    path: str
    chunks: int
    indexed: bool
    # Set only when indexing actually failed (as opposed to the [rag] extras
    # being absent, or the file legitimately producing no chunks) — the file
    # is saved either way, but the UI must be able to tell "indexed nothing
    # because it was blank" apart from "indexing broke".
    index_error: str | None = None


class EmailIngestRequest(BaseModel):
    text: str


class EmailIngestResponse(BaseModel):
    """POST /api/ingest/email result."""

    proposals: int
    archived_path: str


# Moved to backend/rag/email.py so the indexer and this route share one parser
# -- an .eml is now both captured here and extracted for search, and two copies
# would drift silently. Re-exported under the old private name so the existing
# call sites and tests in this module keep working.
_parse_email = parse_email


def _fallback_extraction(parsed: dict[str, Any]) -> dict[str, Any]:
    """Deterministic extraction — same spirit as the briefing's fallback."""
    body: str = parsed["body"]
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    tasks = [match.group(1).strip() for line in lines if (match := BULLET_RE.match(line))]
    contacts = list(dict.fromkeys(EMAIL_ADDR_RE.findall(body)))
    if parsed["sender"] and parsed["sender"] not in contacts:
        contacts.insert(0, parsed["sender"])
    summary = parsed["subject"] or (lines[0][:120] if lines else "captured email")
    return {
        "tasks": tasks[:10],
        "dates": list(dict.fromkeys(ISO_DATE_RE.findall(body))),
        "contacts": contacts[:10],
        "summary": summary,
    }


def _coerce_extraction(raw: str) -> dict[str, Any]:
    """Parse the model's JSON (tolerating code fences); raise on nonsense."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    payload = json.loads(text[start : end + 1])
    return {
        "tasks": [str(item) for item in payload.get("tasks") or []],
        "dates": [str(item) for item in payload.get("dates") or []],
        "contacts": [str(item) for item in payload.get("contacts") or []],
        "summary": str(payload.get("summary") or "").strip() or "captured email",
    }


def _extraction_section(extraction: dict[str, Any]) -> list[str]:
    """The markdown lines the approval would append to the archived email."""
    lines = ["", "## Extracted (Argus proposal)", "", f"**Summary**: {extraction['summary']}"]
    if extraction["dates"]:
        lines.append(f"**Dates**: {', '.join(extraction['dates'])}")
    if extraction["contacts"]:
        lines.append(f"**Contacts**: {', '.join(extraction['contacts'])}")
    if extraction["tasks"]:
        lines += ["", "### Tasks", ""]
        lines += [f"- [ ] {task}" for task in extraction["tasks"]]
    return lines


def _append_diff(original: str, added_lines: list[str]) -> str:
    """A unified diff that appends ``added_lines`` (writer-applicable)."""
    count = len(original.splitlines())
    hunk = "\n".join(f"+{line}" for line in added_lines)
    return f"@@ -{count + 1},0 +{count + 1},{len(added_lines)} @@\n{hunk}"


def _destinations(settings: Settings) -> list[str]:
    """Every folder an ingest may be pointed at, newest-course-aware.

    Derived from the taxonomy plus whatever course folders actually exist, so
    a renamed zone follows automatically and the frontend never builds a vault
    path itself.
    """
    tax = settings.taxonomy
    found = [tax.ingest_files, tax.inbox]
    for zone in (tax.projects, tax.areas, tax.reference, tax.people):
        found.append(zone)
    courses_dir = settings.vault_path / tax.courses
    if courses_dir.is_dir():
        for course in sorted(path.name for path in courses_dir.iterdir() if path.is_dir()):
            found.append(tax.course_materials(course))
            found.append(tax.course_notes(course))
    excluded = tax.excluded_top_dirs
    return [path for path in dict.fromkeys(found) if path.split("/")[0] not in excluded]


def _guarded_target(settings: Settings, target: str) -> str:
    """Validate a caller-supplied destination, or raise the right HTTP error.

    Runs the same guard the write itself will, so a protected or escaping
    target is refused *before* anything is staged rather than failing N items
    later with a file already on disk.
    """
    clean = (target or "").strip().strip("/").replace("\\", "/") or settings.taxonomy.ingest_files
    try:
        guard_user_path(settings.vault_path, f"{clean}/probe.tmp", taxonomy=settings.taxonomy)
    except WriterForbidden as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return clean


def _validate_batch(files: list[UploadFile]) -> list[str]:
    """Check the whole batch before staging any of it.

    All-or-nothing on purpose: a half-accepted batch leaves the user guessing
    which files made it, which is worse than a refusal naming the bad one.
    """
    if not files:
        raise HTTPException(status_code=422, detail="no files were uploaded")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"{len(files)} files is more than one job takes — the limit is "
            f"{MAX_BATCH_FILES}",
        )
    names: list[str] = []
    for upload in files:
        name = upload.filename or "upload.bin"
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported file type {suffix or '(none)'} in {name} — "
                f"accepted: {', '.join(sorted(ALLOWED_SUFFIXES))}",
            )
        names.append(name)
    return names


def _stage_uploads(staging_dir: Path, files: list[UploadFile]) -> None:
    """Copy the uploads to disk before the request ends.

    This is not an optimisation, it is the only thing that makes a 202
    possible: Starlette closes a request's spooled temp files when the request
    scope ends, so `UploadFile` objects handed to a thread that runs later are
    already gone. Copied in chunks rather than read whole, so a 50-file batch
    never sits in memory at once.

    Staging lives under ``.argus/`` and never inside the vault -- a staged file
    in the vault would be picked up by the watcher and indexed at its
    temporary path.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    for index, upload in enumerate(files):
        name = (upload.filename or "upload.bin").replace("/", "_").replace("\\", "_")
        # Ordinal-prefixed so two uploads sharing a filename in one batch do
        # not collide before they reach the vault (where _dedupe handles it).
        destination = staging_dir / f"{index}__{name}"
        written = 0
        upload.file.seek(0)
        with destination.open("wb") as handle:
            while chunk := upload.file.read(COPY_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_FILE_BYTES:
                    handle.close()
                    shutil.rmtree(staging_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"{name} is larger than the {MAX_FILE_BYTES // (1024 * 1024)}MB "
                        "per-file limit — the vault is a git repository, so a file this big "
                        "would be committed into its history",
                    )
                handle.write(chunk)


def _reconcile_on_boot(settings: Settings) -> None:
    """Fail jobs orphaned by a previous process, and drop their staging dirs."""
    try:
        conn = connect(settings.db_path)
    except Exception as exc:  # a vault that isn't configured yet must still boot
        logger.warning("ingest: could not reconcile stale jobs: %s", exc)
        return
    try:
        init_schema(conn)
        reconciled = store.reconcile_stale_jobs(conn)
        if reconciled:
            logger.info("ingest: marked %d job(s) interrupted by restart", reconciled)
    except Exception:
        logger.exception("ingest: reconciling stale jobs failed")
    finally:
        conn.close()
    shutil.rmtree(_staging_root(settings), ignore_errors=True)


def _staging_root(settings: Settings) -> Path:
    return settings.db_path.parent / "ingest-staging"


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a synchronous call."""
    threading.Thread(target=run, daemon=True).start()


def build_ingest_router(
    settings: Settings,
    generator: Generator,
    index_factory: Any,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    """/api/ingest routes. ``generator``, ``index_factory`` and ``job_runner``
    are injectable so tests run without the agent SDK, the embedding model, or
    a background thread racing their assertions."""
    router = APIRouter(prefix="/api")
    run_job = job_runner or _default_job_runner

    # Once per process, at construction: a job left 'running' by a killed
    # process would otherwise be polled forever by whatever page reopens on it.
    # Deliberately not per-connection -- run against this process's own rows it
    # would kill the job currently in flight.
    _reconcile_on_boot(settings)

    @router.post("/ingest", response_model=IngestResponse)
    async def ingest(
        file: UploadFile, target: str | None = Form(default=None)
    ) -> IngestResponse:
        name = file.filename or "upload.bin"
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported file type {suffix or '(none)'} — "
                f"accepted: {', '.join(sorted(ALLOWED_SUFFIXES))}",
            )
        try:
            rel_path = save_ingest_file(
                settings.vault_path,
                target or settings.taxonomy.ingest_files,
                name,
                await file.read(),
                taxonomy=settings.taxonomy,
            )
        except WriterForbidden as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Index with the existing pipeline; missing [rag] extras degrade
        # gracefully — the file is saved either way. A genuine failure (as
        # opposed to the extras being absent) is logged and surfaced in
        # ``index_error`` rather than silently reported as "0 chunks", which
        # used to be indistinguishable from a file that was legitimately empty.
        chunks = 0
        index_error: str | None = None
        try:
            chunks = index_factory().upsert_file(settings.vault_path, rel_path)
        except ImportError as exc:
            logger.warning("ingest indexing unavailable — [rag] extras not installed: %s", exc)
        except Exception as exc:
            logger.exception("ingest indexing failed for %s", rel_path)
            index_error = str(exc)
        return IngestResponse(
            path=rel_path, chunks=chunks, indexed=chunks > 0, index_error=index_error
        )

    @router.post("/ingest/email", response_model=EmailIngestResponse)
    async def ingest_email(request: EmailIngestRequest) -> EmailIngestResponse:
        if not request.text.strip():
            raise HTTPException(status_code=422, detail="email text is empty")
        parsed = _parse_email(request.text)

        try:
            archived = archive_email(
                settings.vault_path,
                parsed["body"],
                subject=parsed["subject"],
                sender=parsed["sender"],
                email_date=parsed["date"],
                taxonomy=settings.taxonomy,
            )
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Claude extraction with a deterministic fallback (briefing pattern).
        try:
            extraction = _coerce_extraction(
                await generator(EXTRACT_PROMPT.format(email=request.text[:12_000]))
            )
        except Exception:
            extraction = _fallback_extraction(parsed)

        # Proposal, never a direct write (I1): one note-diff suggestion that
        # appends the extraction to the archived email, applied by the writer
        # only after approval in the review queue.
        original = (settings.vault_path / archived).read_text(encoding="utf-8")
        diff = _append_diff(original, _extraction_section(extraction))
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            queue.insert_suggestion(
                conn,
                "note",
                {"path": archived, "diff": diff},
                f"email capture: {extraction['summary'][:120]} "
                f"({len(extraction['tasks'])} task(s), {len(extraction['dates'])} date(s))",
            )
        finally:
            conn.close()
        return EmailIngestResponse(proposals=1, archived_path=archived)

    @router.get("/sources", response_model=SourcesResponse)
    def sources(folder: str | None = None) -> SourcesResponse:
        """Every file in the vault RAG can read, with its chunk count."""
        counts: dict[str, int] | None = None
        try:
            counts = index_factory().chunk_counts()
        except Exception as exc:
            # No [rag] extras, or a broken chroma directory. The files are
            # still real and still listable; they just report unknown counts.
            # Same posture as /api/index/status, which exists for this reason.
            logger.warning("sources: chunk counts unavailable: %s", exc)
        return SourcesResponse(
            sources=list_sources(
                settings.vault_path,
                taxonomy=settings.taxonomy,
                chunk_counts=counts,
                folder=folder,
                suffixes=settings.taxonomy.indexable_suffixes,
            ),
            index_available=counts is not None,
        )

    @router.get("/ingest/destinations", response_model=DestinationsResponse)
    def destinations() -> DestinationsResponse:
        return DestinationsResponse(destinations=_destinations(settings))

    @router.get("/ingest/note-styles", response_model=NoteStylesResponse)
    def note_style_options() -> NoteStylesResponse:
        return NoteStylesResponse(
            styles=[
                NoteStyleInfo(key=style.key, label=style.label, description=style.description)
                for style in note_styles.NOTE_STYLES.values()
            ]
        )

    @router.post("/ingest/precheck", response_model=PrecheckResponse)
    def precheck(request: PrecheckRequest) -> PrecheckResponse:
        target = _guarded_target(settings, request.target)
        safe_name = SAFE_NAME_RE.sub("_", request.filename).strip() or "upload.bin"
        rel_path = f"{target}/{safe_name}"
        existing = settings.vault_path / rel_path
        if not existing.is_file():
            return PrecheckResponse(exists=False)
        digest = hashlib.sha256()
        with existing.open("rb") as handle:
            while chunk := handle.read(COPY_CHUNK_BYTES):
                digest.update(chunk)
        return PrecheckResponse(exists=True, path=rel_path, sha256=digest.hexdigest())

    @router.post("/ingest/jobs", status_code=202, response_model=JobAccepted)
    def start_job(
        files: Annotated[list[UploadFile], File()] = None,
        target: Annotated[str | None, Form()] = None,
        summary_prompt: Annotated[str, Form()] = "",
        note_style: Annotated[str, Form()] = "",
        replace: Annotated[bool, Form()] = False,
    ) -> JobAccepted:
        """Accept a batch, stage it, and hand it to a background job."""
        uploads = files or []
        clean_target = _guarded_target(settings, target or "")
        names = _validate_batch(uploads)
        # Validated here rather than in the job: a style the user cannot have
        # chosen means a broken client, and 202-ing it would ingest the batch
        # with no note at all, which looks exactly like the feature failing.
        try:
            style = note_styles.resolve_style(note_style)
        except note_styles.NoteStyleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            # One job at a time: two would mean two embedding models loaded at
            # once and two concurrent `git add -A` runs, and _git_snapshot runs
            # git with check=False, so the loser of that race fails silently.
            if store.running_job_id(conn) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="an ingest is already running — wait for it to finish",
                )
            job_id = store.create_job(
                conn,
                target=clean_target,
                summary_prompt=(summary_prompt or "").strip(),
                filenames=names,
                note_style=style.key if style else "",
            )
        finally:
            conn.close()

        staging_dir = _staging_root(settings) / job_id
        try:
            _stage_uploads(staging_dir, uploads)
        except Exception:
            conn = connect(settings.db_path)
            try:
                store.finish_job(conn, job_id, status="failed", error="upload failed")
            finally:
                conn.close()
            raise

        run_job(
            lambda: run_ingest_job(
                job_id,
                settings=settings,
                index_factory=index_factory,
                generator=generator,
                staging_dir=staging_dir,
                replace=replace,
            )
        )
        return JobAccepted(job_id=job_id)

    @router.get("/ingest/jobs")
    def list_ingest_jobs() -> dict[str, Any]:
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            return {"jobs": store.list_jobs(conn)}
        finally:
            conn.close()

    @router.get("/ingest/jobs/{job_id}")
    def get_ingest_job(job_id: str) -> dict[str, Any]:
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            job = store.get_job(conn, job_id)
        finally:
            conn.close()
        if job is None:
            raise HTTPException(status_code=404, detail=f"no ingest job {job_id}")
        return job

    return router
