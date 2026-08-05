"""The morning briefing: what today looks like, assembled from the vault.

Data assembly and rendering are deterministic; an optional ``composer``
(the agent, in production) may rewrite the briefing as prose, and any
composer failure falls back to the deterministic render so 07:00 never
produces nothing.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.connectors import gcal
from backend.connectors.gcal import CalendarEvent
from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.vault.tasks import TaskItem, bucketed_tasks, parse_task_line, refresh_cache

logger = logging.getLogger(__name__)

EXAM_RE = re.compile(r"\b(exam|quiz|midterm|final)\b", re.IGNORECASE)
MAX_WEAK_TOPICS = 5
UNCHECKED_RE = re.compile(r"^\s*[-*]\s+\[ \]\s+(.*)$")

Composer = Callable[["BriefingData"], str]

#: Predicate over a vault-relative note path: may this note's content leave
#: this machine? Only the outward-facing surface supplies one (see
#: :func:`briefing_data`); local callers pass nothing.
NoteVisible = Callable[[str | None], bool]


class BriefingData(BaseModel):
    """Everything the 07:00 briefing talks about."""

    date: str
    events: list[CalendarEvent]
    due_today: list[TaskItem]
    overdue: list[TaskItem]
    yesterday_unfinished: list[str]
    exam_countdowns: list[dict[str, Any]]
    weak_topics: list[str]


def _yesterday_unfinished(
    vault_path: Path,
    today: date,
    *,
    taxonomy: Taxonomy | None = None,
    note_visible: NoteVisible | None = None,
) -> list[str]:
    tax = taxonomy or active_taxonomy()
    rel = f"{tax.daily}/{(today - timedelta(days=1)).isoformat()}.md"
    note = vault_path / rel
    if not note.is_file():
        return []
    if note_visible is not None and not note_visible(rel):
        return []
    unfinished = []
    for line in note.read_text(encoding="utf-8").splitlines():
        task = parse_task_line(line)
        if task is not None and not task.done:
            unfinished.append(task.text)
    return unfinished


def _exam_countdowns(buckets: dict[str, list[TaskItem]], today: date) -> list[dict[str, Any]]:
    countdowns = []
    for task in buckets["today"] + buckets["week"] + buckets["someday"]:
        haystack = task.text + " " + " ".join(task.tags)
        if task.due and EXAM_RE.search(haystack):
            days_left = (date.fromisoformat(task.due) - today).days
            countdowns.append({"title": task.text, "due": task.due, "days_left": days_left})
    return sorted(countdowns, key=lambda item: item["days_left"])


def _weak_topics(
    vault_path: Path,
    *,
    taxonomy: Taxonomy | None = None,
    note_visible: NoteVisible | None = None,
) -> list[str]:
    tax = taxonomy or active_taxonomy()
    topics = []
    for queue_file in sorted(vault_path.glob(tax.review_queue_glob)):
        if note_visible is not None:
            rel = queue_file.relative_to(vault_path).as_posix()
            if not note_visible(rel):
                continue
        for line in queue_file.read_text(encoding="utf-8").splitlines():
            match = UNCHECKED_RE.match(line)
            if match:
                topics.append(match.group(1).strip())
    return topics[:MAX_WEAK_TOPICS]


def briefing_data(
    settings: Settings,
    conn: sqlite3.Connection,
    today: date,
    *,
    note_visible: NoteVisible | None = None,
) -> BriefingData:
    """Assemble the day's facts. Connectors degrade to empty when unconfigured.

    ``note_visible`` is an optional per-note predicate applied to every vault
    source before anything is derived from it. It exists for the outward-facing
    surface (``/api/external/briefing``), which must satisfy the full I3 check
    rather than the directory-only filtering the task cache does.

    It is applied to the **buckets**, not to the finished ``BriefingData``,
    because filtering the output is not equivalent: ``exam_countdowns`` derives
    its titles from the same tasks, so a post-filter on ``due_today`` and
    ``overdue`` alone would still leak the text of a withheld task. Filtering
    the input makes every derived field correct at once.

    Local callers pass nothing and behave exactly as before.
    """
    vault = settings.vault_path
    tax = settings.taxonomy
    refresh_cache(conn, vault, taxonomy=tax)
    buckets = bucketed_tasks(conn, today=today)
    if note_visible is not None:
        buckets = {
            name: [task for task in items if note_visible(task.path)]
            for name, items in buckets.items()
        }
    events, _gcal_error = gcal.list_events_safe(today)
    return BriefingData(
        date=today.isoformat(),
        events=events,
        due_today=buckets["today"],
        overdue=buckets["overdue"],
        yesterday_unfinished=_yesterday_unfinished(
            vault, today, taxonomy=tax, note_visible=note_visible
        ),
        exam_countdowns=_exam_countdowns(buckets, today),
        weak_topics=_weak_topics(vault, taxonomy=tax, note_visible=note_visible),
    )


def render_briefing(data: BriefingData) -> str:
    """Deterministic markdown briefing; empty sections are omitted."""
    parts: list[str] = []
    if data.events:
        lines = [
            f"- {event.start[-5:] if not event.all_day else 'all day'} {event.title}"
            for event in data.events
        ]
        parts.append("**Schedule**\n" + "\n".join(lines))
    if data.due_today:
        parts.append("**Due today**\n" + "\n".join(f"- {t.text}" for t in data.due_today))
    if data.overdue:
        parts.append(
            "**Overdue**\n" + "\n".join(f"- {t.text} (was due {t.due})" for t in data.overdue)
        )
    if data.yesterday_unfinished:
        parts.append(
            "**Carried over from yesterday**\n"
            + "\n".join(f"- {text}" for text in data.yesterday_unfinished)
        )
    if data.exam_countdowns:
        parts.append(
            "**Exam countdown**\n"
            + "\n".join(
                f"- {item['title']} — {item['days_left']} day(s) left ({item['due']})"
                for item in data.exam_countdowns
            )
        )
    if data.weak_topics:
        parts.append(
            "**Weak topics to review**\n" + "\n".join(f"- {topic}" for topic in data.weak_topics)
        )
    if not parts:
        parts.append("Nothing scheduled, nothing due — a clean slate.")
    return f"Briefing for {data.date}\n\n" + "\n\n".join(parts)


def _composer_prompt(data: BriefingData) -> str:
    return (
        "You are Argus writing the user's morning briefing for their daily note.\n"
        "Rewrite the facts below as a short, warm, scannable markdown briefing "
        "(bold section labels, bullet lists, no H1/H2 headings). Do not invent "
        "events or tasks; keep every date exactly as given.\n\n"
        f"FACTS:\n{render_briefing(data)}\n\nDATA (JSON):\n{data.model_dump_json(indent=1)}"
    )


def make_agent_composer(settings: Settings) -> Composer:
    """The production composer, bound to this install's model registry.

    A factory rather than a plain function because :data:`Composer` takes only
    the briefing data, leaving nowhere to pass ``settings`` — and without
    ``settings`` :func:`~backend.agent.generate.agent_generate` never consults
    the registry at all. That is what used to pin the 07:00 job to the Claude
    Code CLI: on a machine set up entirely for Ollama it raised, the caller
    swallowed it, and the user got the deterministic template every morning
    with no error and no usage row.
    """

    def compose(data: BriefingData) -> str:
        import asyncio

        from backend.agent.generate import agent_generate

        text = asyncio.run(
            agent_generate(
                _composer_prompt(data),
                feature="briefing",
                db_path=settings.db_path,
                settings=settings,
            )
        ).strip()
        if not text:
            raise RuntimeError("composer returned empty text")
        return text

    return compose


def compose_briefing(
    settings: Settings,
    conn: sqlite3.Connection,
    composer: Composer | None = None,
    today: date | None = None,
) -> str:
    """Build the briefing, letting ``composer`` write prose when available."""
    resolved_today = today or date.today()
    data = briefing_data(settings, conn, resolved_today)
    if composer is not None:
        from backend.telemetry.audit import log_prompt_conn

        try:
            text = composer(data)
        except Exception as exc:  # a dead agent must never kill the 07:00 job
            # ...but it must not be invisible either. This ran unattended at
            # 07:00 and silently produced the deterministic template forever on
            # any machine where the agent could not start.
            logger.warning(
                "briefing composer failed, falling back to the plain render: %s", exc
            )
        else:
            # Audited only on success, and against the model that actually ran.
            # Logging before the call claimed opus had read these paths even
            # when the composer never started.
            yesterday_note = (
                f"{settings.taxonomy.daily}/{(resolved_today - timedelta(days=1)).isoformat()}.md"
            )
            queue_paths = [
                path.relative_to(settings.vault_path).as_posix()
                for path in settings.vault_path.glob(settings.taxonomy.review_queue_glob)
            ]
            log_prompt_conn(
                conn, "briefing", settings.default_model, [yesterday_note, *queue_paths]
            )
            return text
    return render_briefing(data)
