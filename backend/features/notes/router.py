"""Vault browsing and user-initiated note CRUD.

The reads (``/api/vault``, ``/api/notes``) came over from the app factory; the
mutations are a thin HTTP layer over the single writer (I1).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.notes.relink import relinkable_notes, run_relink_job
from backend.rag.deindex import forget_paths
from backend.vault import writer
from backend.vault.errors import raise_http
from backend.vault.notes import NoteInfo, list_notes
from backend.vault.writer import WriterConflict, WriterError, guard_user_path


class VaultInfo(BaseModel):
    """Vault identity, plus taxonomy-derived paths the frontend must not
    hardcode (see ``backend.core.taxonomy.Taxonomy`` — a literal ``30-Areas``
    here would reintroduce the bug the configurable-taxonomy refactor fixed)."""

    name: str
    #: Absolute path to the vault root. `name` alone cannot build a working
    #: `obsidian://` link -- see backend.vault.obsidian for why -- so the
    #: frontend needs this to deep-link at all. `name` stays for display.
    path: str
    papers_dir: str
    highlights_path: str
    # Same reasoning as papers_dir/highlights_path: Study mode's course
    # folder must come from here, not a hardcoded `15-Courses` literal.
    courses_dir: str


class NoteContent(BaseModel):
    path: str
    content: str


class NoteCreate(BaseModel):
    path: str
    content: str


class NoteUpdate(BaseModel):
    path: str
    expected_content: str
    new_content: str


class RelinkStarted(BaseModel):
    """The 202 body for ``POST /api/notes/relink``."""

    job_id: str
    #: How many generated notes the job will visit. Reported up front because
    #: "nothing happened" and "you have no generated notes" look identical in
    #: a progress readout otherwise, and the second is the state a brand-new
    #: vault is actually in.
    notes: int


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a synchronous call."""
    threading.Thread(target=run, daemon=True).start()


def build_notes_router(
    settings: Settings,
    index_factory: Callable[[], Any] | None = None,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    """Vault reads and note CRUD.

    ``index_factory`` is here for two routes: deleting a note has to delete
    its chunks too, or the note keeps being retrieved and cited in chat after
    the file is gone, and a relink has to find each note's neighbours and
    re-embed what it rewrote. It is injected the same way the ingest and
    study routers take it, and defaults to ``None`` so a caller that has no
    index (or no ``[rag]`` extras) still gets working note CRUD -- relink is
    the one route that then answers 503 rather than pretending.

    ``job_runner`` mirrors the ingest, study and index routers: a daemon
    thread in production, a synchronous call in tests.
    """
    router = APIRouter(prefix="/api")
    run_job = job_runner or _default_job_runner

    @router.get("/vault", response_model=VaultInfo)
    def vault_info() -> VaultInfo:
        return VaultInfo(
            name=settings.vault_path.name,
            path=settings.vault_path.as_posix(),
            papers_dir=settings.taxonomy.papers_dir,
            highlights_path=settings.taxonomy.paper_highlights_note,
            courses_dir=settings.taxonomy.courses,
        )

    @router.get("/notes", response_model=list[NoteInfo], response_model_exclude_none=True)
    def notes(folder: str | None = None, fields: str | None = None) -> list[NoteInfo]:
        """``folder``/``fields`` are optional (see ``list_notes``); omitting
        both keeps this byte-identical to the pre-existing unfiltered listing."""
        keys = [key.strip() for key in fields.split(",") if key.strip()] if fields else None
        return list_notes(
            settings.vault_path, taxonomy=settings.taxonomy, folder=folder, frontmatter_keys=keys
        )

    @router.get("/note", response_model=NoteContent)
    def get_note(path: str) -> NoteContent:
        try:
            resolved = guard_user_path(settings.vault_path, path, taxonomy=settings.taxonomy)
        except WriterError as exc:
            raise_http(exc)
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"{path} does not exist")
        return NoteContent(path=path, content=resolved.read_text(encoding="utf-8"))

    @router.post("/note/create", response_model=NoteContent, status_code=201)
    def create_note(request: NoteCreate) -> NoteContent:
        try:
            rel_path = writer.create_note(
                settings.vault_path, request.path, request.content, taxonomy=settings.taxonomy
            )
        except WriterError as exc:
            raise_http(exc)
        return NoteContent(path=rel_path, content=request.content)

    @router.put("/note", response_model=NoteContent)
    def put_note(request: NoteUpdate) -> NoteContent:
        try:
            writer.update_note(
                settings.vault_path,
                request.path,
                request.expected_content,
                request.new_content,
                taxonomy=settings.taxonomy,
            )
        except WriterConflict as exc:
            current = guard_user_path(
                settings.vault_path, request.path, taxonomy=settings.taxonomy
            ).read_text(encoding="utf-8")
            raise_http(exc, current_content=current)
        except WriterError as exc:
            raise_http(exc)
        return NoteContent(path=request.path, content=request.new_content)

    @router.delete("/note")
    def remove_note(path: str) -> dict:
        """Delete one note, and the chunks that made it answer questions.

        The de-index used to be missing entirely: the file was unlinked and
        every chunk of it stayed in the index, so search and chat went on
        retrieving and citing a note that no longer existed. It runs *after*
        the unlink and never fails the request — see
        :mod:`backend.rag.deindex`.
        """
        try:
            writer.delete_note(settings.vault_path, path, taxonomy=settings.taxonomy)
        except WriterError as exc:
            raise_http(exc)
        return {"path": path, "chunks_removed": forget_paths(index_factory, [path])}

    @router.post("/notes/relink", status_code=202, response_model=RelinkStarted)
    def relink_notes() -> RelinkStarted:
        """Re-derive relationships for every note Argus wrote.

        Takes the index slot: it re-embeds each note it rewrites and takes a
        git snapshot, so it contends with an ingest and a reindex. 409 is this
        codebase's idiom for "one at a time" -- the same answer
        ``/api/index/reindex`` gives when an ingest is in the way.

        Answers 202 and hands back a ``job_id``: the walk plus a rewrite plus
        an embed per note is far too long for a request, and the job feeds the
        same segmented readout an ingest uses.
        """
        if index_factory is None:
            raise HTTPException(
                status_code=503,
                detail="the search index is unavailable — relinking needs it to find neighbours",
            )
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            blocking = store.running_job(conn, "relink")
            if blocking is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"a {blocking['kind']} is already using the index — "
                    "wait for it to finish",
                )
            job_id = store.create_job(
                conn,
                # A relink writes only into notes that already exist, so it
                # has no target folder; '' is the column's "not applicable".
                target="",
                filenames=[],
                kind="relink",
            )
        finally:
            conn.close()

        # Counted here as well as inside the job, which is one vault walk more
        # than strictly necessary. The alternative is a 202 that cannot say
        # whether it found anything, and a user staring at an empty progress
        # panel with no way to tell "still starting" from "you have no
        # generated notes yet".
        notes = len(relinkable_notes(settings.vault_path, taxonomy=settings.taxonomy))
        run_job(lambda: run_relink_job(job_id, settings=settings, index_factory=index_factory))
        return RelinkStarted(job_id=job_id, notes=notes)

    return router
