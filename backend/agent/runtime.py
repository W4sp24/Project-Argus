"""Argus's chat agent: provider-agnostic, with in-process vault tools.

Tools are read-only (P1 agent) and every result carries the metadata the model
needs for citations (invariant I6). Which engine runs them depends on the
registry entry the caller names — the Claude Code CLI on the user's
subscription (invariant I5, still the default), the Anthropic API on a key, or
any OpenAI-compatible endpoint including local Ollama. See
:mod:`backend.agent.adapters`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

from backend.agent.adapters import (
    TextDelta,
    ToolSpec,
    UsageReported,
    json_schema,
    resolve_adapter,
    text_result,
)
from backend.audit import log_prompt
from backend.config import Settings
from backend.rag.index import VaultIndex
from backend.rag.paths import is_indexable

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "chat.md"
MAX_NOTE_CHARS = 20_000
MAX_TURNS = 8
DISALLOWED_TOOLS = ("Bash", "Write", "Edit")  # read-only agent (P1)


def _tool_text(payload: Any) -> dict[str, Any]:
    """Wrap a payload as an MCP text content result."""
    return text_result(payload)


def build_vault_tools(
    settings: Settings, index: VaultIndex, model_label: str = MODEL
) -> list[ToolSpec]:
    """The read-only tool belt shared by chat, and re-exposed over MCP.

    ``model_label`` only labels the audit rows (§ ``/api/audit`` reports which
    model read which paths), so it follows whichever model actually ran.
    """

    async def search_vault(args: dict[str, Any]) -> dict[str, Any]:
        from backend.rag.retrieve import retrieve

        hits = retrieve(
            index,
            str(args["query"]),
            settings.vault_path,
            k=8,
            course=str(args["course"]) if args.get("course") else None,
        )
        if not hits:
            return _tool_text({"results": [], "note": "no matches in the vault"})
        log_prompt(
            settings.db_path,
            "chat",
            model_label,
            [str(hit["meta"].get("path")) for hit in hits if hit["meta"].get("path")],
        )
        return _tool_text(
            {
                "results": [
                    {
                        "text": hit["text"][:1500],
                        "path": hit["meta"].get("path"),
                        "title": hit["meta"].get("title"),
                        "heading": hit["meta"].get("heading"),
                        "page": hit["meta"].get("page"),
                        "slide": hit["meta"].get("slide"),
                    }
                    for hit in hits
                ]
            }
        )

    async def read_note(args: dict[str, Any]) -> dict[str, Any]:
        rel_path = str(args["path"]).replace("\\", "/")
        if not is_indexable(rel_path):
            return _tool_text("error: that path is not readable")
        file_path = settings.vault_path / rel_path
        if not file_path.is_file():
            return _tool_text(f"error: no note at {rel_path}")
        log_prompt(settings.db_path, "chat", model_label, [rel_path])
        return _tool_text(file_path.read_text(encoding="utf-8", errors="ignore")[:MAX_NOTE_CHARS])

    async def list_tasks(_args: dict[str, Any]) -> dict[str, Any]:
        from backend.db import connect, init_schema
        from backend.tasks.parser import bucketed_tasks, refresh_cache

        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            refresh_cache(conn, settings.vault_path)
            buckets = bucketed_tasks(conn, today=date.today())
        finally:
            conn.close()
        return _tool_text(
            {bucket: [task.model_dump() for task in tasks] for bucket, tasks in buckets.items()}
        )

    return [
        ToolSpec(
            name="search_vault",
            description=(
                "Hybrid semantic+keyword search over the user's vault (notes and course "
                "materials). Call this before answering anything about the user's life, "
                "notes, or courses. Returns chunks with path/page/slide for citations."
            ),
            parameters=json_schema(
                {
                    "query": {"type": "string", "description": "what to search for"},
                    "course": {"type": "string", "description": "optional course code filter"},
                },
                required=["query"],
            ),
            handler=search_vault,
        ),
        ToolSpec(
            name="read_note",
            description=(
                "Read one full markdown note from the vault by its vault-relative path "
                "(as returned by search_vault)."
            ),
            parameters=json_schema({"path": {"type": "string"}}),
            handler=read_note,
        ),
        ToolSpec(
            name="list_tasks",
            description=(
                "List the user's tasks from the vault, bucketed into overdue / today / "
                "week / someday. Takes no arguments."
            ),
            parameters=json_schema({}),
            handler=list_tasks,
        ),
    ]


class ChatAgent:
    """Streams RAG-grounded chat answers. One instance per app process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._index = VaultIndex(settings.db_path.parent / "chroma")

    def warm(self) -> None:
        """Load the embedding model + chroma now, off the chat hot path.

        The first vault tool call otherwise pays ~20s of model loading inside
        the agent's event loop.
        """
        import contextlib

        # Warming is best-effort; real errors surface on actual queries.
        with contextlib.suppress(Exception):
            self._index.query("warmup", n_results=1)

    def _resolve_model(self, model: str | None) -> str:
        """Map a registry model name (§7) onto the id that will actually run.

        No ``model`` keeps today's behavior (``MODEL`` on the Claude Code
        path). A named model must exist in the registry; every registered
        provider now routes for real.
        """
        if not model:
            return MODEL
        entry = next((m for m in self._settings.models if m["name"] == model), None)
        if entry is None:
            raise RuntimeError(f"unknown model {model!r} — register it under /system first")
        return str(entry.get("model_id") or entry["name"])

    async def stream_chat(self, message: str, model: str | None = None) -> AsyncIterator[str]:
        """Yield text deltas for one user message, on whichever backend is chosen."""
        from backend.usage import record_result_usage

        resolved_model = self._resolve_model(model)
        adapter = resolve_adapter(
            self._settings,
            model,
            tool_namespace="argus",
            disallowed_tools=DISALLOWED_TOOLS,
            fallback_model=MODEL,
        )
        tools = build_vault_tools(self._settings, self._index, model_label=resolved_model)

        async for event in adapter.run(
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            user_message=message,
            tools=tools,
            max_turns=MAX_TURNS,
        ):
            if isinstance(event, TextDelta):
                yield event.text
            elif isinstance(event, UsageReported):
                # Fire-and-forget usage logging (§14) — never breaks chat.
                record_result_usage(self._settings.db_path, "chat", event, model=resolved_model)
