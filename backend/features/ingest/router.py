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

import frontmatter
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import notes as note_styles
from backend.features.ingest import store
from backend.features.ingest.pipeline import run_ingest_job
from backend.rag.deindex import forget_paths
from backend.rag.email import parse_email
from backend.vault import suggestions as queue
from backend.vault.errors import raise_http
from backend.vault.privacy import is_private_path
from backend.vault.sources import SourceInfo, generated_kind, list_sources
from backend.vault.writer import (
    SAFE_NAME_RE,
    WriterError,
    WriterForbidden,
    WriterMissing,
    archive_email,
    delete_note,
    guard_user_path,
    log_action,
    save_ingest_file,
    snapshot_vault,
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


class SourceDeleteRequest(BaseModel):
    """``DELETE /api/sources``: which files to remove from the vault.

    ``include_generated`` also removes the note Argus wrote *from* each file
    (its ``.notes.md`` / ``.summary.md`` companion), which is off by default:
    a user re-uploading a corrected PDF may well want to keep the notes they
    have since annotated.
    """

    paths: list[str]
    include_generated: bool = False


class SourceDeleteSummary(BaseModel):
    """A truthful report of what a source delete actually removed.

    Same shape of promise as
    :class:`backend.features.study.router.CourseDeleteSummary`: real counts of
    what went, not an echo of what was asked for. ``chunks_removed`` is 0 when
    the index was unavailable — the files are still gone.
    """

    files_removed: int
    notes_removed: int
    chunks_removed: int
    #: Exactly which vault paths no longer exist, sources and companion notes
    #: together, so the caller can drop those rows without re-fetching.
    removed: list[str]


class DestinationsResponse(BaseModel):
    """``GET /api/ingest/destinations`` — where an ingest may be pointed.

    Built from :class:`~backend.core.taxonomy.Taxonomy`, never concatenated in
    the frontend: a literal ``15-Courses/<CODE>/materials`` in the UI is the
    exact bug the configurable-taxonomy refactor fixed.
    """

    destinations: list[str]


class LimitsResponse(BaseModel):
    """``GET /api/ingest/limits`` — what the server will actually accept.

    The frontend mirrors these so it can reject a 400 MB `.zip` in no time at
    all rather than after the upload, but a mirrored constant is a second copy
    of a rule and goes stale the day this set changes. Served for the same
    reason destinations and note styles are: the definition lives on the side
    that enforces it, and the client asks.
    """

    #: Lowercased, dot-prefixed, sorted so the list is stable to diff.
    suffixes: list[str]
    max_files: int
    max_file_bytes: int


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


class JobItem(BaseModel):
    """One file inside a job, as the progress readout renders it."""

    id: int
    filename: str
    path: str | None = None
    stage: str
    chunks: int
    summary_path: str | None = None
    error: str | None = None
    failed_stage: str | None = None


class JobSummary(BaseModel):
    """One row of ``GET /api/ingest/jobs`` — no items, by design.

    Declared as a model rather than left to ``dict[str, Any]`` because these
    two routes are about to gain clients beyond the ingest panel (study
    generations and reindexes are jobs now), and an undeclared response is a
    contract that only exists in ``web/lib/api.ts``.
    """

    id: str
    created_at: str
    finished_at: str | None = None
    status: str
    kind: str
    #: Inputs and results with no column of their own — a guide's ``scope``, an
    #: exam's ``exam_id``. ``None`` for a plain ingest.
    params: dict[str, Any] | None = None
    target: str
    summary_prompt: str
    note_style: str
    total: int
    done: int
    error: str | None = None


class JobDetail(JobSummary):
    """``GET /api/ingest/jobs/{job_id}`` — the summary plus every file."""

    items: list[JobItem]


class JobListResponse(BaseModel):
    jobs: list[JobSummary]


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


def _guard_deletable(settings: Settings, rel_path: str) -> Path:
    """Both halves of the path check, before a single file is unlinked.

    ``guard_user_path`` is the write guard (I3) and judges ``parts[0]`` only,
    so ``15-Courses/CS301/99-Private/x.pdf`` passes it while
    :func:`~backend.vault.privacy.is_private_path` — which checks *every*
    segment — refuses it. The two disagree, and a delete endpoint taking
    caller-supplied paths must satisfy both: ``/sources`` never lists such a
    file, so the disagreement is not reachable through the UI today, but this
    route is reachable by anything that can speak to the API.

    Existence is checked here too, so a batch naming a file that is already
    gone 404s before the snapshot rather than after two of its siblings have
    been unlinked.
    """
    tax = settings.taxonomy
    try:
        resolved = guard_user_path(settings.vault_path, rel_path, taxonomy=tax)
    except WriterError as exc:
        raise_http(exc)
    if is_private_path(rel_path, taxonomy=tax):
        raise HTTPException(
            status_code=403,
            detail=f"{rel_path} is inside a protected zone and cannot be deleted",
        )
    if not resolved.is_file():
        raise_http(WriterMissing(f"{rel_path} does not exist"))
    return resolved


def _companion_note(settings: Settings, rel_path: str) -> str | None:
    """The note Argus generated *from* this source, when both halves agree.

    Provenance is recorded, not guessed: the note writer puts the exact
    vault-relative source path into the generated note's ``source``
    frontmatter. So the resolution is two steps that must both hold:

    1. compute the candidate with the same :func:`note_destination` rule that
       wrote it — O(1), and correct across ``_dedupe``'s renames because both
       sides derive from the deduped stem;
    2. read that file's frontmatter and confirm its ``source`` is this exact
       path.

    A note that does not claim this source is somebody else's note and is left
    alone, whatever its name. A source that is itself a generated note has no
    companion of its own — asked via ``generated_kind``, the one definition of
    that convention.
    """
    if generated_kind(rel_path) is not None:
        return None
    candidate = note_styles.note_destination(rel_path, taxonomy=settings.taxonomy)
    note = settings.vault_path / candidate
    if not note.is_file():
        return None
    try:
        claimed = frontmatter.load(note).metadata.get("source")
    except Exception as exc:
        logger.warning("sources: could not read the frontmatter of %s: %s", candidate, exc)
        return None
    if claimed != rel_path:
        logger.info("sources: %s does not claim %s as its source — leaving it", candidate, rel_path)
        return None
    return candidate


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

    @router.delete("/sources", response_model=SourceDeleteSummary)
    def delete_sources(request: SourceDeleteRequest) -> SourceDeleteSummary:
        """Remove files from the vault **and** from the index, together.

        A file removed from Obsidian that still answers chat questions is the
        worst half-state this feature can produce, so the two halves are one
        request. The order is deliberate:

        1. **guard every path first, all-or-nothing.** A batch containing one
           protected path is refused whole, naming the offender — the same
           precedent ``_validate_batch`` sets for uploads, for the same
           reason: a half-applied delete leaves the user guessing which files
           survived, which is worse than a refusal.
        2. **one snapshot for the whole batch.** Never one per file:
           ``_git_snapshot`` runs git with ``check=False``, so two snapshots
           race on ``.git/index.lock`` and the loser fails *silently* — I2
           broken with nothing to show for it.
        3. **unlink**, through the single writer (I1). ``delete_note`` is not
           markdown-specific; it guards and unlinks any path in a
           user-editable zone.
        4. **de-index last.** A crash after the snapshot leaves a recoverable
           vault. A crash after de-indexing but before the unlink would leave
           a file that exists and is silently unsearchable; this way round the
           worst case is a file that is gone but still indexed, which any
           future re-index of that path repairs.

        There is no trash and no soft-delete anywhere in this app: the
        pre-delete git commit is the undo, which is what every confirm dialog
        already promises.
        """
        paths = list(dict.fromkeys(path.strip() for path in request.paths if path.strip()))
        if not paths:
            raise HTTPException(status_code=422, detail="no paths were given")

        for rel_path in paths:
            _guard_deletable(settings, rel_path)

        companions: list[str] = []
        if request.include_generated:
            companions = [
                companion
                for rel_path in paths
                if (companion := _companion_note(settings, rel_path)) is not None
                and companion not in paths
            ]
        targets = paths + list(dict.fromkeys(companions))

        # The reason describes the *intent*, which is what a pre-apply
        # snapshot is a point before; the audit line below reports what
        # actually went, which is not the same thing if the loop breaks.
        intent = f"{len(paths)} source(s)"
        if len(targets) > len(paths):
            intent += f" and {len(targets) - len(paths)} generated note(s)"
        snapshot_vault(settings.vault_path, f"delete {intent}")
        removed: list[str] = []
        try:
            for rel_path in targets:
                # snapshot/log off: one undo point and one audit line for one
                # user action, taken around the loop rather than inside it.
                delete_note(
                    settings.vault_path,
                    rel_path,
                    taxonomy=settings.taxonomy,
                    snapshot=False,
                    log=False,
                )
                removed.append(rel_path)
        except WriterError as exc:
            raise_http(exc)
        finally:
            if removed:
                log_action(
                    settings.vault_path,
                    f"deleted {len(removed)} file(s): {', '.join(removed)}",
                    taxonomy=settings.taxonomy,
                )

        asked_for = set(paths)
        sources_removed = sum(1 for rel_path in removed if rel_path in asked_for)
        return SourceDeleteSummary(
            files_removed=sources_removed,
            notes_removed=len(removed) - sources_removed,
            chunks_removed=forget_paths(index_factory, removed),
            removed=removed,
        )

    @router.get("/ingest/destinations", response_model=DestinationsResponse)
    def destinations() -> DestinationsResponse:
        return DestinationsResponse(destinations=_destinations(settings))

    @router.get("/ingest/limits", response_model=LimitsResponse)
    def limits() -> LimitsResponse:
        return LimitsResponse(
            suffixes=sorted(ALLOWED_SUFFIXES),
            max_files=MAX_BATCH_FILES,
            max_file_bytes=MAX_FILE_BYTES,
        )

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
            # Kind-aware: a reindex holds the same slot, because it loads the
            # same embedding model and writes the same chroma directory. That
            # is new -- the two used to hold independent locks and could run
            # at once. See `store.SLOT_GROUPS`.
            blocking = store.running_job(conn, "ingest")
            if blocking is not None:
                what = "a reindex" if blocking["kind"] == "reindex" else "an ingest"
                raise HTTPException(
                    status_code=409,
                    detail=f"{what} is already running — wait for it to finish",
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

    @router.get("/ingest/jobs", response_model=JobListResponse)
    def list_ingest_jobs(kind: str = "ingest") -> dict[str, Any]:
        """Recent jobs of one ``kind``; ``kind=all`` for every kind.

        Defaults to ``ingest`` rather than to everything: this table now also
        holds reindexes and study generations, and the history panel that
        reads this route asks for ingest history. Widening the default would
        have changed what an untouched frontend displays.
        """
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            return {"jobs": store.list_jobs(conn, kind=None if kind == "all" else kind)}
        finally:
            conn.close()

    @router.get("/ingest/jobs/{job_id}", response_model=JobDetail)
    def get_ingest_job(job_id: str) -> dict[str, Any]:
        """One job of any kind, by id — this is the poll endpoint for all of them."""
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
