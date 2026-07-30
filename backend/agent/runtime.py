"""Argus's chat agent: provider-agnostic, with in-process vault tools.

Tools are read-only (P1 agent) and every result carries the metadata the model
needs for citations (invariant I6). Which engine runs them depends on the
registry entry the caller names — the Claude Code CLI on the user's
subscription (invariant I5, still the default), the Anthropic API on a key, or
any OpenAI-compatible endpoint including local Ollama. See
:mod:`backend.agent.adapters`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
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
from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy
from backend.rag.index import VaultIndex
from backend.telemetry.audit import log_prompt
from backend.vault.paths import is_indexable

logger = logging.getLogger("argus.rag")

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "chat.md"
MAX_NOTE_CHARS = 20_000
MAX_TURNS = 8
DISALLOWED_TOOLS = ("Bash", "Write", "Edit")  # read-only agent (P1)
# Upper bound on waiting for the background index warm before searching anyway.
# Generous: a cold embedding-model load is ~20s, and proceeding early means a
# failed search rather than a slow one.
WARM_TIMEOUT_SECONDS = 90.0


def _load_system_prompt(taxonomy: Taxonomy) -> str:
    """``chat.md`` with the taxonomy's folder names templated in.

    The prompt tells the model which folder is off-limits (I3); if the
    taxonomy is configurable, a prompt naming the wrong folder would actively
    mislead the model into thinking a *different* (possibly non-private)
    folder is the protected one. Simple string substitution, not str.format,
    so the markdown's own braces (none today, but future-proof) are never at
    risk of a KeyError.
    """
    return PROMPT_PATH.read_text(encoding="utf-8").replace("{{PRIVATE_DIR}}", taxonomy.private)


def _tool_text(payload: Any) -> dict[str, Any]:
    """Wrap a payload as an MCP text content result."""
    return text_result(payload)


def build_vault_tools(
    settings: Settings,
    index: VaultIndex,
    model_label: str = MODEL,
    ready: threading.Event | None = None,
) -> list[ToolSpec]:
    """The read-only tool belt shared by chat, and re-exposed over MCP.

    ``model_label`` only labels the audit rows (§ ``/api/audit`` reports which
    model read which paths), so it follows whichever model actually ran.

    ``ready``, when given, is set once :meth:`ChatAgent.warm` has finished
    loading the embedding model on its background thread. ``search_vault``
    waits on it, because using the chroma client from the event loop while that
    load is still in flight fails with a bare
    ``'RustBindingsAPI' object has no attribute 'bindings'``.

    This only became reachable when non-Claude providers arrived: warming
    starts when the chat socket connects, and the Claude Code CLI's spin-up was
    always slow enough to hide it. A local Ollama model answers immediately and
    loses the race, so the first question after opening chat would come back
    with no citations at all. ``None`` means nothing is warming (the MCP server
    and tests), so nothing waits.
    """

    async def _await_index() -> None:
        if ready is not None and not ready.is_set():
            # to_thread, not Event.wait directly: blocking the event loop here
            # would also stall the deltas already streaming to the browser.
            await asyncio.to_thread(ready.wait, WARM_TIMEOUT_SECONDS)

    async def search_vault(args: dict[str, Any]) -> dict[str, Any]:
        from backend.rag.retrieve import retrieve

        await _await_index()
        # to_thread, not a direct call: retrieve() is synchronous and can take
        # seconds against a cold index. On the MCP stdio server that would stall
        # the whole event loop; in chat it would stall the deltas already
        # streaming to the browser.
        hits = await asyncio.to_thread(
            retrieve,
            index,
            str(args["query"]),
            settings.vault_path,
            k=8,
            course=str(args["course"]) if args.get("course") else None,
            taxonomy=settings.taxonomy,
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
        if not is_indexable(rel_path, taxonomy=settings.taxonomy):
            return _tool_text("error: that path is not readable")
        file_path = settings.vault_path / rel_path
        if not file_path.is_file():
            return _tool_text(f"error: no note at {rel_path}")
        log_prompt(settings.db_path, "chat", model_label, [rel_path])
        return _tool_text(file_path.read_text(encoding="utf-8", errors="ignore")[:MAX_NOTE_CHARS])

    async def list_tasks(_args: dict[str, Any]) -> dict[str, Any]:
        from backend.core.db import connect, init_schema
        from backend.vault.tasks import bucketed_tasks, refresh_cache

        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
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
        self._index = VaultIndex(settings.db_path.parent / "chroma", taxonomy=settings.taxonomy)
        # Set when warming finishes, so vault searches can wait rather than
        # race the still-initializing chroma client. See build_vault_tools.
        self._ready = threading.Event()

    def warm(self) -> None:
        """Load the embedding model + chroma now, off the chat hot path.

        The first vault tool call otherwise pays ~20s of model loading inside
        the agent's event loop.
        """
        try:
            # Warming is best-effort; real errors surface on actual queries.
            # Not swallowed silently, though — a warm that fails every single
            # time (a genuinely broken index, not just "not built yet") used
            # to leave no trace anywhere a person would look.
            self._index.query("warmup", n_results=1)
        except Exception:
            logger.warning(
                "chat index warm-up failed (will retry on first real query)", exc_info=True
            )
        finally:
            # Always release waiters, including when warming failed — a search
            # that then fails on its own is far better than one that hangs.
            self._ready.set()

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
        from backend.telemetry.usage import record_result_usage

        resolved_model = self._resolve_model(model)
        adapter = resolve_adapter(
            self._settings,
            model,
            tool_namespace="argus",
            disallowed_tools=DISALLOWED_TOOLS,
            fallback_model=MODEL,
        )
        tools = build_vault_tools(
            self._settings, self._index, model_label=resolved_model, ready=self._ready
        )

        recorded = False
        try:
            async for event in adapter.run(
                system_prompt=_load_system_prompt(self._settings.taxonomy),
                user_message=message,
                tools=tools,
                max_turns=MAX_TURNS,
            ):
                if isinstance(event, TextDelta):
                    yield event.text
                elif isinstance(event, UsageReported):
                    # Fire-and-forget usage logging (§14) — never breaks chat.
                    record_result_usage(
                        self._settings.db_path, "chat", event, model=resolved_model
                    )
                    recorded = True
        finally:
            # Every adapter yields UsageReported *last*, so closing the tab
            # mid-answer used to drop the whole turn — and the abandoned answers
            # are the long, expensive ones. The tokens were already billed by
            # the provider, so bank what the adapter counted before we stopped
            # reading. record_result_usage swallows its own errors, which is
            # what makes it safe to call while unwinding.
            if not recorded:
                partial = getattr(adapter, "partial_usage", lambda: None)()
                if partial is not None and any(partial.usage.values()):
                    record_result_usage(
                        self._settings.db_path, "chat", partial, model=resolved_model
                    )
