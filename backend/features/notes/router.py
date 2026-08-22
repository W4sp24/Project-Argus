"""Vault browsing and user-initiated note CRUD.

The reads (``/api/vault``, ``/api/notes``) came over from the app factory; the
mutations are a thin HTTP layer over the single writer (I1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import Settings
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


def build_notes_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        try:
            writer.delete_note(settings.vault_path, path, taxonomy=settings.taxonomy)
        except WriterError as exc:
            raise_http(exc)
        return {"path": path}

    return router
