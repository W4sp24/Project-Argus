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

from backend import suggestions as queue
from backend.audit import log_prompt_conn
from backend.config import Settings
from backend.db import connect, init_schema
from backend.suggestions import dismissal_feedback
from backend.tasks.parser import bucketed_tasks, refresh_cache

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"
PREFERENCES_NOTE = "30-Areas/assistant-preferences.md"


def _tool_text(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def build_propose_tools(conn: sqlite3.Connection) -> list[Any]:
    """propose_* tools — each inserts a pending suggestion row."""
    from claude_agent_sdk import tool

    @tool(
        "propose_schedule",
        "Propose calendar time blocks (deep work, study, errands, breaks). "
        "blocks_json is a JSON array of {title, start, end} with ISO datetimes.",
        {"blocks_json": str, "rationale": str},
    )
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

    @tool(
        "propose_task_changes",
        "Propose editing one task line in a vault note (reschedule, reprioritize, "
        "break down). old_line must match the note exactly.",
        {"path": str, "line": int, "old_line": str, "new_line": str, "rationale": str},
    )
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

    @tool(
        "propose_note_edit",
        "Propose a unified-diff edit to a vault note (inbox triage, weekly review, "
        "people-note updates).",
        {"path": str, "diff": str, "rationale": str},
    )
    async def propose_note_edit(args: dict[str, Any]) -> dict[str, Any]:
        sid = queue.insert_suggestion(
            conn,
            "note",
            {"path": str(args["path"]), "diff": str(args["diff"])},
            str(args["rationale"]),
        )
        return _tool_text(f"queued suggestion #{sid} for review")

    return [propose_schedule, propose_task_changes, propose_note_edit]


def _planner_context(settings: Settings, conn: sqlite3.Connection, instruction: str) -> str:
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

    log_prompt_conn(conn, "planner", MODEL, sent_paths)

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


async def run_planner(settings: Settings, instruction: str) -> int:
    """Run one planning session; returns the number of suggestions created."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server

    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        before = len(queue.pending(conn))
        server = create_sdk_mcp_server("planner", tools=build_propose_tools(conn))
        options = ClaudeAgentOptions(
            model=MODEL,
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            mcp_servers={"planner": server},
            allowed_tools=[
                "mcp__planner__propose_schedule",
                "mcp__planner__propose_task_changes",
                "mcp__planner__propose_note_edit",
            ],
            disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep"],
            max_turns=12,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query(_planner_context(settings, conn, instruction))
            async for _message in client.receive_response():
                pass  # proposals happen via tools; the text summary is discarded
        return len(queue.pending(conn)) - before
    finally:
        conn.close()
