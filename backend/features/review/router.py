"""Review-queue endpoints: list, approve, dismiss, and trigger the planner."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.vault import suggestions as queue
from backend.vault.suggestions import Suggestion
from backend.vault.writer import WriterError, apply_suggestion

PlannerRunner = Callable[[Settings, str], Awaitable[int]]


class DismissRequest(BaseModel):
    reason: str = ""


class PlanRequest(BaseModel):
    instruction: str = "Plan my day"
    # A registry model name (§7). Omitted keeps the default backend, so every
    # existing client keeps working without sending this.
    model: str | None = None


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
        # Same shape as the /ws/chat bridge: model-aware planners get the
        # model, single-argument test fakes keep working. The TypeError can
        # only be a signature mismatch — it is raised when the coroutine is
        # created, before anything inside it runs.
        if request.model:
            try:
                call = planner(settings, request.instruction, request.model)
            except TypeError:
                call = planner(settings, request.instruction)
        else:
            call = planner(settings, request.instruction)
        return PlanResponse(created=await call)

    return router
