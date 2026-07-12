"""Agenda, task-board, and quick-capture endpoints."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import Settings
from backend.connectors import gcal, todoist
from backend.connectors.gcal import CalendarEvent
from backend.db import connect, init_schema
from backend.tasks.parser import TaskItem, bucket_of, bucketed_tasks, refresh_cache
from backend.writer import WriterError, append_capture


class AgendaResponse(BaseModel):
    """Everything the Today view needs for one date."""

    date: str
    events: list[CalendarEvent]
    tasks: list[TaskItem]
    top_tasks: list[TaskItem]
    configured: dict[str, bool]


class CaptureRequest(BaseModel):
    text: str


class CaptureResponse(BaseModel):
    path: str


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
        external = [task for task in todoist.list_tasks() if not task.done]
        due_external = [task for task in external if task.due and task.due <= target.isoformat()]
        day_tasks = vault_today + due_external
        top = day_tasks[:3] if day_tasks else buckets["week"][:3]

        return AgendaResponse(
            date=target.isoformat(),
            events=gcal.list_events(target),
            tasks=day_tasks,
            top_tasks=top,
            configured={"gcal": gcal.configured(), "todoist": todoist.configured()},
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
        for task in todoist.list_tasks():
            if not task.done:
                buckets[bucket_of(task, today)].append(task)
        return buckets

    @router.post("/capture", response_model=CaptureResponse)
    def capture(request: CaptureRequest) -> CaptureResponse:
        try:
            return CaptureResponse(path=append_capture(settings.vault_path, request.text))
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
