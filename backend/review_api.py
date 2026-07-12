"""Review-queue endpoints: list, approve, dismiss, and trigger the planner."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import suggestions as queue
from backend.config import Settings
from backend.db import connect, init_schema
from backend.suggestions import Suggestion
from backend.writer import WriterError, apply_suggestion

PlannerRunner = Callable[[Settings, str], Awaitable[int]]


class DismissRequest(BaseModel):
    reason: str = ""


class PlanRequest(BaseModel):
    instruction: str = "Plan my day"


class PlanResponse(BaseModel):
    created: int


def build_review_router(settings: Settings, planner: PlannerRunner) -> APIRouter:
    router = APIRouter(prefix="/api")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("/review", response_model=list[Suggestion])
    def review_queue() -> list[Suggestion]:
        conn = db()
        try:
            return queue.pending(conn)
        finally:
            conn.close()

    @router.post("/review/{suggestion_id}/approve", response_model=Suggestion)
    def approve(suggestion_id: int) -> Suggestion:
        conn = db()
        try:
            return apply_suggestion(conn, settings.vault_path, suggestion_id)
        except WriterError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            conn.close()

    @router.post("/review/{suggestion_id}/dismiss", response_model=Suggestion)
    def dismiss(suggestion_id: int, request: DismissRequest) -> Suggestion:
        conn = db()
        try:
            if queue.get(conn, suggestion_id) is None:
                raise HTTPException(status_code=404, detail="no such suggestion")
            queue.dismiss(conn, suggestion_id, request.reason or "dismissed without reason")
            row = queue.get(conn, suggestion_id)
            assert row is not None
            return row
        finally:
            conn.close()

    @router.post("/plan", response_model=PlanResponse)
    async def plan(request: PlanRequest) -> PlanResponse:
        created = await planner(settings, request.instruction)
        return PlanResponse(created=created)

    return router
