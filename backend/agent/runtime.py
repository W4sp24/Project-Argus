"""FRIDAY's chat agent: claude-agent-sdk with in-process vault tools.

Auth is the user's Claude subscription login (invariant I5) — ANTHROPIC_API_KEY
is never set here. Tools are read-only (P1 agent); every result carries the
metadata the model needs for citations (invariant I6).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.rag.index import VaultIndex
from backend.rag.paths import is_indexable

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "chat.md"
MAX_NOTE_CHARS = 20_000


def _tool_text(payload: Any) -> dict[str, Any]:
    """Wrap a payload as an MCP text content result."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def build_vault_tools(settings: Settings, index: VaultIndex) -> list[Any]:
    """The read-only tool belt shared by chat (and later the planner)."""
    from claude_agent_sdk import tool

    @tool(
        "search_vault",
        "Hybrid semantic+keyword search over the user's vault (notes and course "
        "materials). Call this before answering anything about the user's life, "
        "notes, or courses. Returns chunks with path/page/slide for citations.",
        {"query": str, "course": str},
    )
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

    @tool(
        "read_note",
        "Read one full markdown note from the vault by its vault-relative path "
        "(as returned by search_vault).",
        {"path": str},
    )
    async def read_note(args: dict[str, Any]) -> dict[str, Any]:
        rel_path = str(args["path"]).replace("\\", "/")
        if not is_indexable(rel_path):
            return _tool_text("error: that path is not readable")
        file_path = settings.vault_path / rel_path
        if not file_path.is_file():
            return _tool_text(f"error: no note at {rel_path}")
        return _tool_text(file_path.read_text(encoding="utf-8", errors="ignore")[:MAX_NOTE_CHARS])

    @tool(
        "list_tasks",
        "List the user's tasks. (Task engine arrives in Phase P2 — currently a stub.)",
        {},
    )
    async def list_tasks(_args: dict[str, Any]) -> dict[str, Any]:
        return _tool_text(
            "The task engine is not built yet (Phase P2). Tell the user tasks are coming soon."
        )

    return [search_vault, read_note, list_tasks]


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

    async def stream_chat(self, message: str) -> AsyncIterator[str]:
        """Yield text deltas for one user message (I5: subscription auth)."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            StreamEvent,
            TextBlock,
            create_sdk_mcp_server,
        )

        server = create_sdk_mcp_server(
            "friday", tools=build_vault_tools(self._settings, self._index)
        )
        options = ClaudeAgentOptions(
            model=MODEL,
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            mcp_servers={"friday": server},
            allowed_tools=[
                "mcp__friday__search_vault",
                "mcp__friday__read_note",
                "mcp__friday__list_tasks",
            ],
            disallowed_tools=["Bash", "Write", "Edit"],  # read-only agent (P1)
            include_partial_messages=True,
            max_turns=8,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query(message)
            streamed_any = False
            async for event in client.receive_response():
                if isinstance(event, StreamEvent):
                    raw = event.event
                    if raw.get("type") == "content_block_delta":
                        delta = raw.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            streamed_any = True
                            yield delta["text"]
                elif isinstance(event, AssistantMessage) and not streamed_any:
                    for block in event.content:
                        if isinstance(block, TextBlock) and block.text:
                            yield block.text
