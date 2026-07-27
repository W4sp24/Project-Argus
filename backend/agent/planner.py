"""The planner: an agent run whose only writes are suggestion rows (I1).

``propose_*`` tools insert into the suggestions table; the writer executes
them later, after human approval.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from backend.agent.adapters import (
    ToolSpec,
    UsageReported,
    json_schema,
    resolve_adapter,
    text_result,
)
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.telemetry.audit import log_prompt_conn
from backend.vault import suggestions as queue
from backend.vault.suggestions import dismissal_feedback
from backend.vault.tasks import bucketed_tasks, refresh_cache

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"
PREFERENCES_NOTE = "30-Areas/assistant-preferences.md"
MAX_TURNS = 12
# The planner may only ever propose (I1), so it gets no filesystem tools at all.
DISALLOWED_TOOLS = ("Bash", "Write", "Edit", "Read", "Glob", "Grep")


def _tool_text(payload: Any) -> dict[str, Any]:
    return text_result(payload)


def build_propose_tools(conn: sqlite3.Connection) -> list[ToolSpec]:
    """propose_* tools — each inserts a pending suggestion row.

    These are the planner's *only* writes (invariant I1): nothing here touches
    the vault, it queues a row the user approves later. That holds identically
    on every provider, because the handler is the same coroutine whichever
    adapter dispatches it.
    """

    async def propose_schedule(args: dict[str, Any]) -> dict[str, Any]:
        try:
            blocks = json.loads(args["blocks_json"])
            assert isinstance(blocks, list) and blocks
            for block in blocks:
                assert {"title", "start", "end"} <= set(block)
        except Exception:
            return _tool_text("error: blocks_json must be a JSON array of {title,start,end}")
        sid = queue.insert_suggestion(conn, "schedule", {"blocks": blocks}, str(args["rationale"]))
        return _tool_text(f"queued suggestion #{sid} ({len(blocks)} blocks) for review")

    async def propose_task_changes(args: dict[str, Any]) -> dict[str, Any]:
        sid = queue.insert_suggestion(
            conn,
            "task",
            {
                "path": str(args["path"]),
                "line": int(args["line"]),
                "old_line": str(args["old_line"]),
                "new_line": str(args["new_line"]),
            },
            str(args["rationale"]),
        )
        return _tool_text(f"queued suggestion #{sid} for review")

    async def propose_note_edit(args: dict[str, Any]) -> dict[str, Any]:
        sid = queue.insert_suggestion(
            conn,
            "note",
            {"path": str(args["path"]), "diff": str(args["diff"])},
            str(args["rationale"]),
        )
        return _tool_text(f"queued suggestion #{sid} for review")

    return [
        ToolSpec(
            name="propose_schedule",
            description=(
                "Propose calendar time blocks (deep work, study, errands, breaks). "
                "blocks_json is a JSON array of {title, start, end} with ISO datetimes."
            ),
            parameters=json_schema(
                {"blocks_json": {"type": "string"}, "rationale": {"type": "string"}}
            ),
            handler=propose_schedule,
        ),
        ToolSpec(
            name="propose_task_changes",
            description=(
                "Propose editing one task line in a vault note (reschedule, reprioritize, "
                "break down). old_line must match the note exactly."
            ),
            parameters=json_schema(
                {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "old_line": {"type": "string"},
                    "new_line": {"type": "string"},
                    "rationale": {"type": "string"},
                }
            ),
            handler=propose_task_changes,
        ),
        ToolSpec(
            name="propose_note_edit",
            description=(
                "Propose a unified-diff edit to a vault note (inbox triage, weekly review, "
                "people-note updates)."
            ),
            parameters=json_schema(
                {
                    "path": {"type": "string"},
                    "diff": {"type": "string"},
                    "rationale": {"type": "string"},
                }
            ),
            handler=propose_note_edit,
        ),
    ]


def _planner_context(
    settings: Settings, conn: sqlite3.Connection, instruction: str, model_label: str = MODEL
) -> str:
    """Assemble everything the planner needs into one user message."""
    from backend.connectors import gcal, todoist

    today = date.today()
    refresh_cache(conn, settings.vault_path)
    buckets = bucketed_tasks(conn, today=today)

    events = [event.model_dump() for event in gcal.list_events(today)]
    external = [task.model_dump() for task in todoist.list_tasks()]

    sent_paths: list[str] = []
    review_queues: list[str] = []
    for queue_file in settings.vault_path.glob("15-Courses/*/study/review-queue.md"):
        review_queues.append(queue_file.read_text(encoding="utf-8")[-2000:])
        sent_paths.append(queue_file.relative_to(settings.vault_path).as_posix())

    preferences_path = settings.vault_path / PREFERENCES_NOTE
    if preferences_path.is_file():
        preferences = preferences_path.read_text(encoding="utf-8")
        sent_paths.append(PREFERENCES_NOTE)
    else:
        preferences = "(no preferences note yet — assume 50-minute focus blocks, breaks between)"

    log_prompt_conn(conn, "planner", model_label, sent_paths)

    payload = {
        "today": today.isoformat(),
        "fixed_events": events,
        "task_buckets": {k: [t.model_dump() for t in v] for k, v in buckets.items()},
        "todoist_tasks": external,
        "weak_topics_review_queues": review_queues,
        "dismissal_feedback": dismissal_feedback(conn),
    }
    return (
        f"Instruction from the user: {instruction}\n\n"
        f"PREFERENCES NOTE ({PREFERENCES_NOTE}):\n{preferences}\n\n"
        f"CONTEXT (JSON):\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )


def _resolve_model(settings: Settings, model: str | None) -> str:
    """The provider-side id for a registry name; ``MODEL`` when unset."""
    if not model:
        return MODEL
    entry = next((m for m in settings.models if m["name"] == model), None)
    if entry is None:
        raise RuntimeError(f"unknown model {model!r} — register it under /system first")
    return str(entry.get("model_id") or entry["name"])


async def run_planner(settings: Settings, instruction: str, model: str | None = None) -> int:
    """Run one planning session; returns the number of suggestions created.

    ``model`` names a registry entry (§7); omitting it keeps the historical
    Claude Code path, so existing callers are unaffected.
    """
    from backend.telemetry.usage import record_result_usage

    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        before = len(queue.pending(conn))
        resolved_model = _resolve_model(settings, model)
        adapter = resolve_adapter(
            settings,
            model,
            tool_namespace="planner",
            disallowed_tools=DISALLOWED_TOOLS,
            fallback_model=MODEL,
        )
        async for event in adapter.run(
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            user_message=_planner_context(settings, conn, instruction, resolved_model),
            tools=build_propose_tools(conn),
            max_turns=MAX_TURNS,
        ):
            # Proposals happen via tools; the text summary is discarded.
            if isinstance(event, UsageReported):
                record_result_usage(settings.db_path, "planner", event, model=resolved_model)
        return len(queue.pending(conn)) - before
    finally:
        conn.close()
