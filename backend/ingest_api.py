"""Ingestion endpoints (redesign §11): file upload + manual email capture.

Files land in the vault through :mod:`backend.writer` (snapshot-first, I1)
and are then indexed with the existing extract → chunk → embed pipeline.
Email capture is manual by design — text is pasted or an ``.eml`` dropped;
there is deliberately NO IMAP/Gmail sync here. Extractions become proposals
in the review queue (mirroring the planner), never direct writes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from email import message_from_string, policy
from typing import Any

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend import suggestions as queue
from backend.config import Settings
from backend.db import connect, init_schema
from backend.writer import (
    INGEST_FILES_DIR,
    WriterError,
    WriterForbidden,
    archive_email,
    save_ingest_file,
)

Generator = Callable[[str], Awaitable[str]]

ALLOWED_SUFFIXES = {".pdf", ".pptx", ".docx", ".md", ".eml"}
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
BULLET_RE = re.compile(r"^\s*(?:[-*]\s+(?:\[ \]\s+)?|\d+[.)]\s+)(.+)$")

EXTRACT_PROMPT = """You extract actionable structure from one email.
Return ONLY a JSON object, no prose, of the shape:
{{"tasks": ["..."], "dates": ["YYYY-MM-DD ..."], "contacts": ["Name <addr>"], "summary": "..."}}
Tasks are concrete action items for the reader. Keep every date exactly as
written. summary is one sentence.

EMAIL:
{email}
"""


class IngestResponse(BaseModel):
    """POST /api/ingest result: where the file landed and how indexing went."""

    path: str
    chunks: int
    indexed: bool


class EmailIngestRequest(BaseModel):
    text: str


class EmailIngestResponse(BaseModel):
    """POST /api/ingest/email result."""

    proposals: int
    archived_path: str


def _parse_email(text: str) -> dict[str, Any]:
    """Split raw pasted text / .eml content into headers + body (stdlib only)."""
    message = message_from_string(text, policy=policy.default)
    if not (message["From"] or message["Subject"] or message["Date"]):
        return {"body": text, "subject": None, "sender": None, "date": None}
    body = ""
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            body = part.get_content()
    except Exception:
        body = ""
    if not body.strip():  # header-only paste or non-MIME body
        body = message.get_payload() if isinstance(message.get_payload(), str) else text
    email_date = None
    try:
        if message["Date"]:
            from email.utils import parsedate_to_datetime

            email_date = parsedate_to_datetime(message["Date"]).date().isoformat()
    except Exception:
        email_date = None
    return {
        "body": str(body),
        "subject": str(message["Subject"]) if message["Subject"] else None,
        "sender": str(message["From"]) if message["From"] else None,
        "date": email_date,
    }


def _fallback_extraction(parsed: dict[str, Any]) -> dict[str, Any]:
    """Deterministic extraction — same spirit as the briefing's fallback."""
    body: str = parsed["body"]
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    tasks = [match.group(1).strip() for line in lines if (match := BULLET_RE.match(line))]
    contacts = list(dict.fromkeys(EMAIL_ADDR_RE.findall(body)))
    if parsed["sender"] and parsed["sender"] not in contacts:
        contacts.insert(0, parsed["sender"])
    summary = parsed["subject"] or (lines[0][:120] if lines else "captured email")
    return {
        "tasks": tasks[:10],
        "dates": list(dict.fromkeys(ISO_DATE_RE.findall(body))),
        "contacts": contacts[:10],
        "summary": summary,
    }


def _coerce_extraction(raw: str) -> dict[str, Any]:
    """Parse the model's JSON (tolerating code fences); raise on nonsense."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    payload = json.loads(text[start : end + 1])
    return {
        "tasks": [str(item) for item in payload.get("tasks") or []],
        "dates": [str(item) for item in payload.get("dates") or []],
        "contacts": [str(item) for item in payload.get("contacts") or []],
        "summary": str(payload.get("summary") or "").strip() or "captured email",
    }


def _extraction_section(extraction: dict[str, Any]) -> list[str]:
    """The markdown lines the approval would append to the archived email."""
    lines = ["", "## Extracted (Argus proposal)", "", f"**Summary**: {extraction['summary']}"]
    if extraction["dates"]:
        lines.append(f"**Dates**: {', '.join(extraction['dates'])}")
    if extraction["contacts"]:
        lines.append(f"**Contacts**: {', '.join(extraction['contacts'])}")
    if extraction["tasks"]:
        lines += ["", "### Tasks", ""]
        lines += [f"- [ ] {task}" for task in extraction["tasks"]]
    return lines


def _append_diff(original: str, added_lines: list[str]) -> str:
    """A unified diff that appends ``added_lines`` (writer-applicable)."""
    count = len(original.splitlines())
    hunk = "\n".join(f"+{line}" for line in added_lines)
    return f"@@ -{count + 1},0 +{count + 1},{len(added_lines)} @@\n{hunk}"


def build_ingest_router(settings: Settings, generator: Generator, index_factory: Any) -> APIRouter:
    """/api/ingest routes. ``generator`` and ``index_factory`` are injectable."""
    router = APIRouter(prefix="/api")

    @router.post("/ingest", response_model=IngestResponse)
    async def ingest(
        file: UploadFile, target: str | None = Form(default=None)
    ) -> IngestResponse:
        name = file.filename or "upload.bin"
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported file type {suffix or '(none)'} — "
                f"accepted: {', '.join(sorted(ALLOWED_SUFFIXES))}",
            )
        try:
            rel_path = save_ingest_file(
                settings.vault_path, target or INGEST_FILES_DIR, name, await file.read()
            )
        except WriterForbidden as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Index with the existing pipeline; missing [rag] extras degrade
        # gracefully — the file is saved either way.
        chunks = 0
        try:
            chunks = index_factory().upsert_file(settings.vault_path, rel_path)
        except Exception:
            chunks = 0
        return IngestResponse(path=rel_path, chunks=chunks, indexed=chunks > 0)

    @router.post("/ingest/email", response_model=EmailIngestResponse)
    async def ingest_email(request: EmailIngestRequest) -> EmailIngestResponse:
        if not request.text.strip():
            raise HTTPException(status_code=422, detail="email text is empty")
        parsed = _parse_email(request.text)

        try:
            archived = archive_email(
                settings.vault_path,
                parsed["body"],
                subject=parsed["subject"],
                sender=parsed["sender"],
                email_date=parsed["date"],
            )
        except WriterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # Claude extraction with a deterministic fallback (briefing pattern).
        try:
            extraction = _coerce_extraction(
                await generator(EXTRACT_PROMPT.format(email=request.text[:12_000]))
            )
        except Exception:
            extraction = _fallback_extraction(parsed)

        # Proposal, never a direct write (I1): one note-diff suggestion that
        # appends the extraction to the archived email, applied by the writer
        # only after approval in the review queue.
        original = (settings.vault_path / archived).read_text(encoding="utf-8")
        diff = _append_diff(original, _extraction_section(extraction))
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            queue.insert_suggestion(
                conn,
                "note",
                {"path": archived, "diff": diff},
                f"email capture: {extraction['summary'][:120]} "
                f"({len(extraction['tasks'])} task(s), {len(extraction['dates'])} date(s))",
            )
        finally:
            conn.close()
        return EmailIngestResponse(proposals=1, archived_path=archived)

    return router
