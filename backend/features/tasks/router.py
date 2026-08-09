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
from backend.features.automations import sources
from backend.vault import writer
from backend.vault.errors import raise_http
from backend.vault.tasks import TaskItem, bucket_of, bucketed_tasks, refresh_cache
from backend.vault.writer import WriterError, append_capture

#: How many undated external tasks the agenda will carry. Matches the shipped
#: `todoist` template's own `limit`, so the n8n and connector paths agree on
#: roughly how much of a long list reaches the dashboard.
UNDATED_EXTERNAL_LIMIT = 25


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
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
            buckets = bucketed_tasks(conn, today=target)
            # Read the external sources while the connection is still open:
            # they consult the automations widget store first and fall back to
            # the native connectors, so they need it.
            todoist_tasks, todoist_error = sources.open_tasks(conn)
            events, gcal_error = sources.calendar_events(conn, target)
        finally:
            conn.close()

        vault_today = buckets["overdue"] + buckets["today"]
        external = [task for task in todoist_tasks if not task.done]
        horizon = target.isoformat()
        due_external = [task for task in external if task.due and task.due <= horizon]
        # An external task with no due date used to be dropped here. That is
        # what an inbox item *is* in both Todoist and the n8n `tasks` widget —
        # the majority of a normal list — so the filter did not trim the panel,
        # it emptied it. Capped rather than unbounded because the connector
        # (unlike the template, which limits to 25) returns every open task
        # across every project, and TASKS.DUE is a dashboard panel.
        undated_external = [task for task in external if not task.due][:UNDATED_EXTERNAL_LIMIT]
        day_tasks = vault_today + due_external + undated_external
        # `someday` is where an undated capture lands (`bucket_of`), so without
        # it a task the user just typed is written to the vault and then shown
        # nowhere — the capture looks like it did nothing.
        top = next(
            (group[:3] for group in (day_tasks, buckets["week"], buckets["someday"]) if group),
            [],
        )

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
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
            buckets = bucketed_tasks(conn)
            todoist_tasks, _todoist_error = sources.open_tasks(conn)
        finally:
            conn.close()
        today = date.today()
        for task in todoist_tasks:
            if not task.done:
                buckets[bucket_of(task, today)].append(task)
        return buckets

    @router.post("/capture", response_model=CaptureResponse)
    def capture(request: CaptureRequest) -> CaptureResponse:
        try:
            return CaptureResponse(
                path=append_capture(settings.vault_path, request.text, taxonomy=settings.taxonomy)
            )
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/tasks/toggle", response_model=NewLine)
    def toggle(request: TaskLineRef) -> NewLine:
        try:
            new_line = writer.toggle_task_line(
                settings.vault_path,
                request.path,
                request.line,
                request.old_line,
                taxonomy=settings.taxonomy,
            )
        except WriterError as exc:
            raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/update", response_model=NewLine)
    def edit_line(request: TaskLineUpdate) -> NewLine:
        try:
            new_line = writer.update_task_line(
                settings.vault_path,
                request.path,
                request.line,
                request.old_line,
                request.new_line,
                taxonomy=settings.taxonomy,
            )
        except WriterError as exc:
            raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/delete")
    def drop_line(request: TaskLineRef) -> dict:
        try:
            writer.delete_task_line(
                settings.vault_path,
                request.path,
                request.line,
                request.old_line,
                taxonomy=settings.taxonomy,
            )
        except WriterError as exc:
            raise_http(exc)
        return {"ok": True}

    return router
