"""Recent-activity feed: what happened lately, merged from vault + db (read-only)."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from backend.config import Settings
from backend.notes import list_notes
from backend.rag.paths import EXCLUDED_TOP_DIRS


class ActivityEvent(BaseModel):
    when: str  # ISO timestamp, sortable
    kind: str  # note | approval | exam
    title: str
    path: str | None = None


def _sort_key(when: str) -> str:
    """Normalize a mixed ISO-ish timestamp (space or ``T`` separated) for sorting."""
    return when.replace(" ", "T")[:19]


def _as_utc_iso(raw: str) -> str:
    """Normalize a SQLite ``datetime('now')`` string (UTC, no offset) to an
    unambiguous UTC ISO string so the frontend doesn't parse it as local time."""
    if raw.endswith(("Z", "+00:00")):
        return raw
    return raw.replace(" ", "T") + "Z"


def recent_activity(
    settings: Settings, conn: sqlite3.Connection, limit: int = 15
) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []

    notes = [
        note
        for note in list_notes(settings.vault_path)
        if note.path.split("/", 1)[0] not in EXCLUDED_TOP_DIRS
    ][:limit]
    for note in notes:
        events.append(
            ActivityEvent(when=note.modified, kind="note", title=note.title, path=note.path)
        )

    for row in conn.execute(
        "SELECT kind, rationale, applied_at FROM suggestions"
        " WHERE status = 'applied' AND applied_at IS NOT NULL"
        " ORDER BY applied_at DESC LIMIT ?",
        (limit,),
    ):
        events.append(
            ActivityEvent(
                when=_as_utc_iso(row["applied_at"]), kind="approval",
                title=f"approved {row['kind']}: {row['rationale'][:80]}",
            )
        )

    for row in conn.execute(
        "SELECT exams.course, attempts.score, attempts.total, attempts.created_at"
        " FROM attempts JOIN exams ON exams.id = attempts.exam_id"
        " ORDER BY attempts.created_at DESC LIMIT ?",
        (limit,),
    ):
        events.append(
            ActivityEvent(
                when=_as_utc_iso(row["created_at"]), kind="exam",
                title=f"{row['course']} practice exam {row['score']}/{row['total']}",
            )
        )

    events.sort(key=lambda event: _sort_key(event.when), reverse=True)
    return events[:limit]
