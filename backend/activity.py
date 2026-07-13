"""Recent-activity feed: what happened lately, merged from vault + db (read-only)."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from backend.config import Settings
from backend.notes import list_notes


class ActivityEvent(BaseModel):
    when: str  # ISO timestamp, sortable
    kind: str  # note | approval | exam
    title: str
    path: str | None = None


def _sort_key(when: str) -> str:
    """Normalize a mixed ISO-ish timestamp (space or ``T`` separated) for sorting."""
    return when.replace(" ", "T")[:19]


def recent_activity(
    settings: Settings, conn: sqlite3.Connection, limit: int = 15
) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []

    for note in list_notes(settings.vault_path)[:limit]:
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
                when=row["applied_at"], kind="approval",
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
                when=row["created_at"], kind="exam",
                title=f"{row['course']} practice exam {row['score']}/{row['total']}",
            )
        )

    events.sort(key=lambda event: _sort_key(event.when), reverse=True)
    return events[:limit]
