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
    """Vault identity for building obsidian:// deep links client-side."""

    name: str


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
        return VaultInfo(name=settings.vault_path.name)

    @router.get("/notes", response_model=list[NoteInfo])
    def notes() -> list[NoteInfo]:
        return list_notes(settings.vault_path, taxonomy=settings.taxonomy)

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
