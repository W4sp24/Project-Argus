"""User-initiated vault CRUD: thin HTTP layer over the single writer (I1)."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import writer
from backend.config import Settings
from backend.writer import (
    WriterConflict,
    WriterError,
    WriterForbidden,
    WriterMissing,
    guard_user_path,
)


class NoteContent(BaseModel):
    path: str
    content: str


class NoteUpdate(BaseModel):
    path: str
    expected_content: str
    new_content: str


class TaskLineRef(BaseModel):
    path: str
    line: int
    old_line: str


class TaskLineUpdate(TaskLineRef):
    new_line: str


class NewLine(BaseModel):
    new_line: str


def _raise_http(exc: WriterError, current_content: str | None = None) -> NoReturn:
    if isinstance(exc, WriterForbidden):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, WriterMissing):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, WriterConflict):
        detail: object = {"message": str(exc), "current_content": current_content}
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def build_notes_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/note", response_model=NoteContent)
    def get_note(path: str) -> NoteContent:
        try:
            resolved = guard_user_path(settings.vault_path, path)
        except WriterError as exc:
            _raise_http(exc)
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"{path} does not exist")
        return NoteContent(path=path, content=resolved.read_text(encoding="utf-8"))

    @router.put("/note", response_model=NoteContent)
    def put_note(request: NoteUpdate) -> NoteContent:
        try:
            writer.update_note(
                settings.vault_path, request.path, request.expected_content, request.new_content
            )
        except WriterConflict as exc:
            current = guard_user_path(settings.vault_path, request.path).read_text(
                encoding="utf-8"
            )
            _raise_http(exc, current_content=current)
        except WriterError as exc:
            _raise_http(exc)
        return NoteContent(path=request.path, content=request.new_content)

    @router.delete("/note")
    def remove_note(path: str) -> dict:
        try:
            writer.delete_note(settings.vault_path, path)
        except WriterError as exc:
            _raise_http(exc)
        return {"path": path}

    @router.post("/tasks/toggle", response_model=NewLine)
    def toggle(request: TaskLineRef) -> NewLine:
        try:
            new_line = writer.toggle_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/update", response_model=NewLine)
    def edit_line(request: TaskLineUpdate) -> NewLine:
        try:
            new_line = writer.update_task_line(
                settings.vault_path, request.path, request.line, request.old_line, request.new_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/delete")
    def drop_line(request: TaskLineRef) -> dict:
        try:
            writer.delete_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return {"ok": True}

    return router
