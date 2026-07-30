"""Agenda, task-board, quick-capture, and single-task-line editing.

The ``/tasks/toggle`` and ``/tasks/line/*`` endpoints used to live in the notes
router because they go through the same writer; they answer about tasks, so
they belong here. They share the notes router's error mapping via
:mod:`backend.vault.errors`.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.connectors import gcal, todoist
from backend.connectors.gcal import CalendarEvent
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.vault import writer
from backend.vault.errors import raise_http
from backend.vault.tasks import TaskItem, bucket_of, bucketed_tasks, refresh_cache
from backend.vault.writer import WriterError, append_capture


class AgendaResponse(BaseModel):
    """Everything the Today view needs for one date."""

    date: str
    events: list[CalendarEvent]
    tasks: list[TaskItem]
    top_tasks: list[TaskItem]
    configured: dict[str, bool]
    #: Populated only for connectors that failed this request (see
    #: `backend.connectors.ConnectorUnavailable`); additive field, default
    #: empty, so existing clients are unaffected.
    connector_errors: dict[str, str] = {}


class CaptureRequest(BaseModel):
    text: str


class CaptureResponse(BaseModel):
    path: str


class TaskLineRef(BaseModel):
    path: str
    line: int
    old_line: str


class TaskLineUpdate(TaskLineRef):
    new_line: str


class NewLine(BaseModel):
    new_line: str


def build_tasks_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("/agenda", response_model=AgendaResponse)
    def agenda(day: str | None = None) -> AgendaResponse:
        target = date.fromisoformat(day) if day else date.today()
        conn = db()
        try:
            refresh_cache(conn, settings.vault_path)
            buckets = bucketed_tasks(conn, today=target)
        finally:
            conn.close()

        vault_today = buckets["overdue"] + buckets["today"]
        todoist_tasks, todoist_error = todoist.list_tasks_safe()
        external = [task for task in todoist_tasks if not task.done]
        due_external = [task for task in external if task.due and task.due <= target.isoformat()]
        day_tasks = vault_today + due_external
        top = day_tasks[:3] if day_tasks else buckets["week"][:3]

        events, gcal_error = gcal.list_events_safe(target)
        connector_errors: dict[str, str] = {}
        if gcal_error:
            connector_errors["gcal"] = gcal_error
        if todoist_error:
            connector_errors["todoist"] = todoist_error

        return AgendaResponse(
            date=target.isoformat(),
            events=events,
            tasks=day_tasks,
            top_tasks=top,
            configured={"gcal": gcal.configured(), "todoist": todoist.configured()},
            connector_errors=connector_errors,
        )

    @router.get("/tasks")
    def tasks_board() -> dict[str, list[TaskItem]]:
        conn = db()
        try:
            refresh_cache(conn, settings.vault_path)
            buckets = bucketed_tasks(conn)
        finally:
            conn.close()
        today = date.today()
        todoist_tasks, _todoist_error = todoist.list_tasks_safe()
        for task in todoist_tasks:
            if not task.done:
                buckets[bucket_of(task, today)].append(task)
        return buckets

    @router.post("/capture", response_model=CaptureResponse)
    def capture(request: CaptureRequest) -> CaptureResponse:
        try:
            return CaptureResponse(path=append_capture(settings.vault_path, request.text))
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/tasks/toggle", response_model=NewLine)
    def toggle(request: TaskLineRef) -> NewLine:
        try:
            new_line = writer.toggle_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/update", response_model=NewLine)
    def edit_line(request: TaskLineUpdate) -> NewLine:
        try:
            new_line = writer.update_task_line(
                settings.vault_path, request.path, request.line, request.old_line, request.new_line
            )
        except WriterError as exc:
            raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/delete")
    def drop_line(request: TaskLineRef) -> dict:
        try:
            writer.delete_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            raise_http(exc)
        return {"ok": True}

    return router
