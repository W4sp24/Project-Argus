"""Insights endpoints: the study/task rollups and the recent-activity feed.

Split out of the briefing router, which served these purely because both were
built in the same phase. Read-only: everything here is computed from the vault
and the local db.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.insights.activity import ActivityEvent, recent_activity
from backend.features.insights.service import (
    HeatmapResponse,
    InsightsSummary,
    heatmap_summary,
    insights_summary,
)


def build_insights_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/insights", response_model=InsightsSummary)
    def insights() -> InsightsSummary:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return insights_summary(settings, conn)
        finally:
            conn.close()

    @router.get("/insights/heatmap", response_model=HeatmapResponse)
    def insights_heatmap() -> HeatmapResponse:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return heatmap_summary(settings, conn)
        finally:
            conn.close()

    @router.get("/activity", response_model=list[ActivityEvent])
    def activity() -> list[ActivityEvent]:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return recent_activity(settings, conn)
        finally:
            conn.close()

    return router
