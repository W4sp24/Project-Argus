"""AGENT.USAGE endpoints — the multi-agent token report and its registry.

Split out of :mod:`backend.features.system.router` for the same reason the
model registry was: it had grown to cover unrelated subjects, and this is now
the largest of them.

The registry half mirrors ``/api/models`` deliberately — scan before you save,
and deleting something takes everything that belonged to it with it. The
difference is what a "credential" is here: nothing. A log folder is a path, so
there is no keyring involvement and nothing to withhold from a response.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.telemetry import scan
from backend.telemetry.agents import discover as discovery
from backend.telemetry.agents import registry, store
from backend.telemetry.formats import FORMATS, find_format


class ScanRequest(BaseModel):
    path: str
    #: Omitted means "work it out" — the normal case.
    glob: str | None = None


class ScanResult(BaseModel):
    """What the SCAN FOLDER button renders.

    ``ok`` False is a real answer with a readable reason, not an error: an
    empty success would let someone register a folder that will never produce a
    single row.
    """

    ok: bool
    detail: str
    format: str | None = None
    format_label: str | None = None
    glob: str = ""
    files: int = 0
    turns: int = 0
    total_tokens: int = 0
    models: list[str] = []
    first_ts: str | None = None
    last_ts: str | None = None
    #: True when the preview read only part of the matched files.
    sampled: bool = False
    sample_paths: list[str] = []


class CustomAgentRequest(BaseModel):
    name: str
    path: str
    glob: str | None = None
    format: str | None = None


class CustomAgentInfo(BaseModel):
    id: str
    label: str
    path: str
    glob: str
    format: str


class FormatInfo(BaseModel):
    id: str
    label: str
    blurb: str


def build_agent_usage_router(settings: Settings) -> APIRouter:
    """The /api/usage/agents routes. Mounted by the system router under /api."""
    router = APIRouter()

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("/usage/agents", response_model=registry.AgentsUsageReport)
    def usage_agents(range: scan.CliRange = "today") -> registry.AgentsUsageReport:  # noqa: A002
        """Every agent Argus can read, installed or not.

        Undetected agents are reported with zeroes and an install hint rather
        than omitted — the selector renders those dimmed and inert, and a row
        that simply vanished would read as a bug instead of as information.
        """
        conn = db()
        try:
            return registry.agents_report(conn, range, settings)
        finally:
            conn.close()

    @router.get("/usage/agents/formats", response_model=list[FormatInfo])
    def usage_formats() -> list[FormatInfo]:
        """Log shapes Argus can read, so the dialog can name what it matched."""
        return [FormatInfo(id=f.id, label=f.label, blurb=f.blurb) for f in FORMATS]

    @router.post("/usage/agents/scan", response_model=ScanResult)
    def scan_folder(request: ScanRequest) -> ScanResult:
        """Look at a folder and report what is really in it. Saves nothing."""
        raw = request.path.strip()
        if not raw:
            raise HTTPException(status_code=422, detail="a folder path is required")

        found = discovery.discover(discovery.expand(raw), request.glob)
        return ScanResult(
            ok=found.ok,
            detail=found.detail,
            format=found.fmt.id if found.fmt else None,
            format_label=found.fmt.label if found.fmt else None,
            glob=found.glob,
            files=found.files,
            turns=found.turns,
            total_tokens=found.total_tokens,
            models=found.models,
            first_ts=found.first_ts,
            last_ts=found.last_ts,
            sampled=found.sampled,
            sample_paths=found.sample_paths,
        )

    @router.post("/usage/agents/custom", response_model=CustomAgentInfo, status_code=201)
    def add_custom_agent(request: CustomAgentRequest) -> CustomAgentInfo:
        label = request.name.strip()
        if not label:
            raise HTTPException(status_code=422, detail="a name is required")

        agent_id = store.slugify(label)
        if agent_id in store.RESERVED_IDS:
            raise HTTPException(
                status_code=409,
                detail=f"{label!r} collides with a built-in agent — pick another name",
            )
        existing = store.load_sources(settings.agent_sources_file)
        if any(entry["id"] == agent_id for entry in existing):
            raise HTTPException(status_code=409, detail=f"agent {label} already exists")
        if not store.AGENT_ID_RE.match(agent_id):
            raise HTTPException(status_code=422, detail="that name has no usable letters in it")

        root = discovery.expand(request.path)
        fmt = find_format(request.format)
        glob = (request.glob or "").strip()

        # Re-run discovery when the client did not pin both, so a saved source
        # always has a format that was actually observed to work on this folder.
        if fmt is None or not glob:
            found = discovery.discover(root, glob or None)
            if not found.ok or found.fmt is None:
                raise HTTPException(
                    status_code=422, detail=found.detail or "nothing readable in that folder"
                )
            fmt, glob = found.fmt, found.glob

        entry = {
            "id": agent_id,
            "label": label,
            "path": str(root),
            "glob": glob,
            "format": fmt.id,
        }
        store.save_sources(settings.agent_sources_file, [*existing, entry])
        return CustomAgentInfo(**entry)

    @router.delete("/usage/agents/custom/{agent_id}")
    def delete_custom_agent(agent_id: str) -> dict[str, str | int]:
        """Remove a source and every row that belonged to it.

        The purge is the point. Leaving the rows behind would keep them in the
        combined total with nothing in the selector to attribute them to, so
        the parts would visibly stop adding up to the whole.
        """
        existing = store.load_sources(settings.agent_sources_file)
        if not any(entry["id"] == agent_id for entry in existing):
            raise HTTPException(status_code=404, detail=f"no agent {agent_id}")
        store.save_sources(
            settings.agent_sources_file, [e for e in existing if e["id"] != agent_id]
        )

        conn = db()
        try:
            removed = scan.purge_agent(conn, agent_id)
        finally:
            conn.close()
        return {"status": "deleted", "id": agent_id, "rows_removed": removed}

    return router
