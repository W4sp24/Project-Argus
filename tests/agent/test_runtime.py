"""Tests for the chat runtime's vault tools.

Focused on the index-warm gate: `search_vault` must not touch the chroma
client while `ChatAgent.warm()` is still loading the embedding model on its
background thread.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from backend.agent.runtime import ChatAgent, build_vault_tools
from backend.core.config import Settings


class FakeIndex:
    """Stands in for VaultIndex, recording when it was queried."""

    def __init__(self) -> None:
        self.queried_at: list[float] = []

    def query(self, *_args, **_kwargs):
        self.queried_at.append(time.monotonic())
        return {}


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    (vault / "50-Reference").mkdir(parents=True)
    (vault / "50-Reference" / "algorithms.md").write_text("Dijkstra.", encoding="utf-8")
    return Settings(_vault_path=vault)


def tool(tools, name):
    return next(spec for spec in tools if spec.name == name)


@pytest.mark.anyio
async def test_search_waits_for_the_index_warm_to_finish(settings: Settings, monkeypatch) -> None:
    """The bug this guards: a fast local model beat the warm and got no citations.

    Warming starts when the chat socket connects. The Claude Code CLI's spin-up
    always hid the gap; a local Ollama model answers instantly, hits a
    half-initialized chroma client, and the whole answer comes back uncited.
    """
    ready = threading.Event()
    order: list[str] = []

    def fake_retrieve(*_args, **_kwargs):
        order.append("retrieve")
        return []

    monkeypatch.setattr("backend.rag.retrieve.retrieve", fake_retrieve)

    tools = build_vault_tools(settings, FakeIndex(), ready=ready)

    def warm_later() -> None:
        time.sleep(0.15)
        order.append("warm-done")
        ready.set()

    threading.Thread(target=warm_later, daemon=True).start()
    await tool(tools, "search_vault").handler({"query": "dijkstra"})

    assert order == ["warm-done", "retrieve"], "the search ran before warming finished"


@pytest.mark.anyio
async def test_search_does_not_wait_when_nothing_is_warming(
    settings: Settings, monkeypatch
) -> None:
    """MCP and tests pass no event, so the gate must be a no-op there."""
    monkeypatch.setattr("backend.rag.retrieve.retrieve", lambda *a, **k: [])

    tools = build_vault_tools(settings, FakeIndex())  # ready defaults to None

    started = time.monotonic()
    await tool(tools, "search_vault").handler({"query": "dijkstra"})

    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_read_note_is_not_gated(settings: Settings) -> None:
    """read_note never touches the index, so it must not wait on the warm."""
    tools = build_vault_tools(settings, FakeIndex(), ready=threading.Event())  # never set

    started = time.monotonic()
    result = await tool(tools, "read_note").handler({"path": "50-Reference/algorithms.md"})

    assert time.monotonic() - started < 1.0
    assert "Dijkstra" in result["content"][0]["text"]


def test_warm_releases_waiters_even_when_it_fails(settings: Settings) -> None:
    """A failed warm must not leave every search hanging until the timeout."""
    agent = ChatAgent(settings)

    class Exploding:
        def query(self, *_args, **_kwargs):
            raise RuntimeError("chroma is unhappy")

    agent._index = Exploding()
    agent.warm()

    assert agent._ready.is_set()


@pytest.mark.anyio
async def test_read_note_refuses_paths_outside_the_indexable_zones(settings: Settings) -> None:
    """99-Private stays unreadable through the tool belt (privacy invariant)."""
    private = settings.vault_path / "99-Private"
    private.mkdir(parents=True, exist_ok=True)
    (private / "secrets.md").write_text("nope", encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    result = await tool(tools, "read_note").handler({"path": "99-Private/secrets.md"})

    assert "not readable" in result["content"][0]["text"]


@pytest.mark.anyio
async def test_list_tasks_returns_real_vault_buckets(settings: Settings) -> None:
    """It was a "not built yet" stub long after backend/tasks/ shipped."""
    (settings.vault_path / "20-Projects").mkdir(parents=True, exist_ok=True)
    (settings.vault_path / "20-Projects" / "work.md").write_text(
        "- [ ] file the registrar form \U0001f4c5 2026-07-30\n", encoding="utf-8"
    )

    tools = build_vault_tools(settings, FakeIndex())
    result = await tool(tools, "list_tasks").handler({})
    buckets = json.loads(result["content"][0]["text"])

    assert {"overdue", "today", "week", "someday"} <= set(buckets)
    everything = [task["text"] for tasks in buckets.values() for task in tasks]
    assert "file the registrar form" in everything


def test_build_vault_tools_exposes_only_read_only_tools(settings: Settings) -> None:
    names = {spec.name for spec in build_vault_tools(settings, FakeIndex())}
    assert names == {"search_vault", "read_note", "list_tasks"}
    assert not any(name.startswith("propose_") for name in names)


def test_resolve_model_keeps_the_default_path(settings: Settings) -> None:
    from backend.agent.runtime import MODEL

    assert ChatAgent(settings)._resolve_model(None) == MODEL
