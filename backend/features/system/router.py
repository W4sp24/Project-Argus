"""SYSTEM tab endpoints (redesign §12/§14): doctor, token usage, prompt audit.

Doctor wraps the existing read-only checks. Usage aggregates the
``token_usage`` table (Argus's own chat/planner/study-generate calls); the
sibling ``/usage/cli`` endpoint aggregates account-wide Claude Code CLI usage
parsed from local ``~/.claude/projects/**/*.jsonl`` transcripts — a distinct
data source, intentionally not combined into one grand total.

``/audit`` came over from the briefing router: it reports which vault files
were sent to a model, never their text (I3), which is diagnostics, not
briefing. Three registries live next door and are mounted here: models
(:mod:`backend.features.system.models`), integrations
(:mod:`backend.features.integrations.router`), and the multi-agent usage
report (:mod:`backend.features.system.agent_usage`).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.integrations.router import build_integrations_router
from backend.features.system.agent_usage import build_agent_usage_router
from backend.features.system.doctor import Check, run_checks
from backend.features.system.models import Prober, Puller, build_models_router
from backend.telemetry import claude_cli
from backend.telemetry.audit import AuditEntry, recent
from backend.telemetry.usage import Range, UsageReport, usage_report


def build_system_router(
    settings: Settings,
    prober: Prober | None = None,
    puller: Puller | None = None,
) -> APIRouter:
    """All /api system routes, including the mounted model-registry router."""
    router = APIRouter(prefix="/api")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.post("/doctor", response_model=list[Check])
    def doctor() -> list[Check]:
        """Run the existing health checks (read-only against the vault)."""
        return run_checks(settings)

    @router.get("/usage", response_model=UsageReport)
    def usage(range: Range = "session") -> UsageReport:  # noqa: A002 - API param name
        conn = db()
        try:
            return usage_report(conn, range)
        finally:
            conn.close()

    @router.get("/usage/cli", response_model=claude_cli.CliUsageReport)
    def usage_cli(range: claude_cli.CliRange = "today") -> claude_cli.CliUsageReport:  # noqa: A002
        """Claude Code only. Kept for the desktop smoke test and older clients;
        ``/usage/agents`` is the one the UI reads."""
        conn = db()
        try:
            return claude_cli.cli_usage_report(conn, range, root=claude_cli.DEFAULT_CLAUDE_HOME)
        finally:
            conn.close()

    @router.get("/audit", response_model=list[AuditEntry])
    def audit_log() -> list[AuditEntry]:
        conn = db()
        try:
            return recent(conn)
        finally:
            conn.close()

    router.include_router(build_models_router(settings, prober, puller))
    router.include_router(build_integrations_router(settings))
    router.include_router(build_agent_usage_router(settings))
    return router
