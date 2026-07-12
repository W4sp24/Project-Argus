"""Briefing endpoints: trigger the morning briefing, read today's back."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.audit import AuditEntry, recent
from backend.briefing import Composer, compose_briefing
from backend.config import Settings
from backend.db import connect, init_schema
from backend.insights import InsightsSummary, insights_summary
from backend.writer import BRIEFING_HEADING, write_briefing


class BriefingResponse(BaseModel):
    date: str
    path: str
    markdown: str


def read_briefing_section(settings: Settings, today: str) -> str | None:
    """Extract the ``## Briefing`` section body from today's daily note."""
    note = settings.vault_path / "10-Daily" / f"{today}.md"
    if not note.is_file():
        return None
    lines = note.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == BRIEFING_HEADING), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def build_briefing_router(settings: Settings, composer: Composer | None) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/briefing/run", response_model=BriefingResponse)
    def run_briefing() -> BriefingResponse:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            markdown = compose_briefing(settings, conn, composer=composer)
        finally:
            conn.close()
        path = write_briefing(settings.vault_path, markdown)
        return BriefingResponse(date=date.today().isoformat(), path=path, markdown=markdown)

    @router.get("/briefing", response_model=BriefingResponse)
    def get_briefing() -> BriefingResponse:
        today = date.today().isoformat()
        markdown = read_briefing_section(settings, today)
        if markdown is None:
            raise HTTPException(status_code=404, detail="no briefing yet today")
        return BriefingResponse(date=today, path=f"10-Daily/{today}.md", markdown=markdown)

    @router.get("/insights", response_model=InsightsSummary)
    def insights() -> InsightsSummary:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return insights_summary(settings, conn)
        finally:
            conn.close()

    @router.get("/audit", response_model=list[AuditEntry])
    def audit_log() -> list[AuditEntry]:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return recent(conn)
        finally:
            conn.close()

    return router
