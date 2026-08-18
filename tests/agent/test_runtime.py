"""Tests for the chat runtime's vault tools.

Focused on the index-warm gate: `search_vault` must not touch the chroma
client while `ChatAgent.warm()` is still loading the embedding model on its
background thread.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from backend.agent.runtime import ChatAgent, _load_system_prompt, build_vault_tools
from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy


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


@pytest.mark.anyio
async def test_search_vault_forces_a_fixed_course_over_the_models_own_arg(
    settings: Settings, monkeypatch
) -> None:
    """Course Hub chat passes a fixed course; the model's own `course` tool
    argument must never widen or redirect that scope — the whole point of
    asking a question from inside one course's hub."""
    seen: list[str | None] = []
    monkeypatch.setattr(
        "backend.rag.retrieve.retrieve", lambda *a, **k: seen.append(k.get("course")) or []
    )

    tools = build_vault_tools(settings, FakeIndex(), course="CS201")
    await tool(tools, "search_vault").handler({"query": "midterm", "course": "CS999"})

    assert seen == ["CS201"], "the fixed course must win over the model-supplied one"


@pytest.mark.anyio
async def test_search_vault_falls_back_to_the_models_course_arg_when_unfixed(
    settings: Settings, monkeypatch
) -> None:
    """Global chat (no fixed course) must keep letting the model decide."""
    seen: list[str | None] = []
    monkeypatch.setattr(
        "backend.rag.retrieve.retrieve", lambda *a, **k: seen.append(k.get("course")) or []
    )

    tools = build_vault_tools(settings, FakeIndex())  # no fixed course
    await tool(tools, "search_vault").handler({"query": "midterm", "course": "CS201"})

    assert seen == ["CS201"]


def test_the_mcp_bridge_warms_its_index_like_chat_does(settings: Settings, monkeypatch) -> None:
    """The 07-27 warm fix reached chat but never reached the MCP bridge.

    Without it the ~20s embedding-model load happens inline on the first
    ``search_vault`` — and because ``retrieve`` is synchronous, that stalls the
    whole stdio event loop with no diagnostic the calling agent can see.
    """
    from backend.agent import mcp_server

    warmed: list[str] = []

    class RecordingIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def query(self, *_args, **_kwargs):
            warmed.append("warm")
            return {}

    monkeypatch.setattr("backend.rag.index.VaultIndex", RecordingIndex)

    tools = mcp_server.read_only_tools(settings)

    assert {spec.name for spec in tools} == set(mcp_server.READ_ONLY_TOOLS)
    for _ in range(50):  # the warm runs on a daemon thread
        if warmed:
            break
        time.sleep(0.02)
    assert warmed, "the MCP bridge must warm the index instead of paying for it inline"


# --- usage survives an abandoned answer -------------------------------------


class _AbandonableAdapter:
    """An adapter whose usage only arrives after the deltas, as all three do."""

    model = "fake-model"

    def __init__(self) -> None:
        self._counted = {"input_tokens": 0, "output_tokens": 0}

    def partial_usage(self):
        from backend.agent.adapters import UsageReported

        return UsageReported(**self._counted)

    async def run(self, **_kwargs):
        from backend.agent.adapters import TextDelta, UsageReported

        self._counted["input_tokens"] = 400
        self._counted["output_tokens"] = 300
        yield TextDelta("a long ")
        self._counted["output_tokens"] = 800
        yield TextDelta("expensive answer")
        yield UsageReported(**self._counted)


def _usage_rows(settings: Settings) -> list[tuple]:
    from backend.core.db import connect, init_schema

    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        return [
            (row["feature"], row["input_tokens"], row["output_tokens"])
            for row in conn.execute("SELECT * FROM token_usage")
        ]
    finally:
        conn.close()


@pytest.mark.anyio
async def test_abandoning_the_stream_still_records_the_tokens(
    settings: Settings, monkeypatch
) -> None:
    """Closing the tab mid-answer used to drop the whole turn's tokens.

    Every adapter yields UsageReported *last*, and the WS handler returned on
    disconnect without draining, so the provider billed for an answer that never
    reached the usage dashboard — biased towards the long, expensive answers
    people actually abandon.
    """
    adapter = _AbandonableAdapter()
    monkeypatch.setattr("backend.agent.runtime.resolve_adapter", lambda *a, **k: adapter)
    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", lambda *a, **k: [])

    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    stream = agent.stream_chat("explain everything")

    assert await stream.__anext__() == "a long "  # then the user walks away
    await stream.aclose()

    rows = _usage_rows(settings)
    assert rows == [("chat", 400, 300)], "the abandoned turn's tokens were lost"


@pytest.mark.anyio
async def test_a_completed_stream_records_usage_exactly_once(
    settings: Settings, monkeypatch
) -> None:
    """The drain must not double-bill a turn that finished normally."""
    adapter = _AbandonableAdapter()
    monkeypatch.setattr("backend.agent.runtime.resolve_adapter", lambda *a, **k: adapter)
    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", lambda *a, **k: [])

    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    assert [chunk async for chunk in agent.stream_chat("hi")] == ["a long ", "expensive answer"]

    assert _usage_rows(settings) == [("chat", 400, 800)]


def test_resolve_model_keeps_the_default_path(settings: Settings) -> None:
    from backend.agent.runtime import MODEL

    assert ChatAgent(settings)._resolve_model(None) == MODEL


@pytest.mark.anyio
async def test_stream_chat_forwards_course_to_the_tool_belt(
    settings: Settings, monkeypatch
) -> None:
    """The plumbing from the ws frame down to the tool belt: `stream_chat`'s
    `course` argument must reach `build_vault_tools`, which is what actually
    fixes `search_vault`'s scope (see the course-forcing tests above)."""
    captured = {}

    def fake_build_vault_tools(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", fake_build_vault_tools)
    monkeypatch.setattr(
        "backend.agent.runtime.resolve_adapter", lambda *a, **k: _AbandonableAdapter()
    )

    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    async for _ in agent.stream_chat("what's on the exam", course="CS201"):
        pass

    assert captured.get("course") == "CS201"


def test_the_system_prompt_names_the_configured_private_dir() -> None:
    """I3 depends on it: a prompt naming the wrong folder tells the model the
    protected zone is somewhere it isn't."""
    prompt = _load_system_prompt(Taxonomy.from_env({"VAULT_PRIVATE_DIR": "Secrets"}))

    assert "Secrets/" in prompt
    assert "{{PRIVATE_DIR}}" not in prompt


def test_the_system_prompt_carries_todays_date() -> None:
    """Without it the model resolves "this week" against its training cutoff."""
    prompt = _load_system_prompt(Taxonomy(), today=date(2026, 8, 18))

    assert "2026-08-18" in prompt
    assert "{{TODAY}}" not in prompt


# --- tool summarizers --------------------------------------------------------


@pytest.mark.anyio
async def test_search_vault_summary_carries_the_query_and_cited_paths_i6(
    settings: Settings, monkeypatch
) -> None:
    """Invariant I6 (every citation traces to a tool result) becomes
    machine-checkable here: the summary's paths are exactly what a chip would
    show, parsed back out of the same JSON the model was handed."""
    hits = [
        {"meta": {"path": "50-Reference/algorithms.md"}, "text": "Dijkstra."},
        {"meta": {"path": "50-Reference/graphs.md"}, "text": "BFS."},
    ]
    monkeypatch.setattr("backend.rag.retrieve.retrieve", lambda *a, **k: hits)

    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "search_vault")
    args = {"query": "graph search"}
    result = await spec.handler(args)
    summary = spec.summarize(args, result["content"][0]["text"])

    assert summary.label == "search_vault"
    assert summary.detail == "graph search"
    assert summary.paths == ("50-Reference/algorithms.md", "50-Reference/graphs.md")
    assert summary.ok is True


@pytest.mark.anyio
async def test_search_vault_summary_reports_an_empty_search_as_ok(
    settings: Settings, monkeypatch
) -> None:
    """Finding nothing is a successful search, not a failed call."""
    monkeypatch.setattr("backend.rag.retrieve.retrieve", lambda *a, **k: [])

    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "search_vault")
    args = {"query": "nonexistent topic"}
    result = await spec.handler(args)
    summary = spec.summarize(args, result["content"][0]["text"])

    assert summary.ok is True
    assert "no matches" in summary.detail
    assert summary.paths == ()


@pytest.mark.anyio
async def test_read_note_summary_flags_an_error_result_as_not_ok(settings: Settings) -> None:
    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "read_note")
    args = {"path": "99-Private/secrets.md"}
    result = await spec.handler(args)
    summary = spec.summarize(args, result["content"][0]["text"])

    assert summary.ok is False
    assert summary.detail == "99-Private/secrets.md"
    assert summary.paths == ("99-Private/secrets.md",)


def test_search_vault_summary_handles_non_json_result_text_without_raising(
    settings: Settings,
) -> None:
    """A handler can return a bare string; the summarizer must not raise."""
    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "search_vault")

    summary = spec.summarize({"query": "whatever"}, "not json at all")

    assert summary.label == "search_vault"
    assert summary.paths == ()
    assert summary.ok is True


def test_search_vault_summary_paths_are_capped_and_deduped(settings: Settings) -> None:
    from backend.agent.runtime import MAX_SUMMARY_PATHS

    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "search_vault")
    result_text = json.dumps(
        {
            "results": [
                {"path": "same.md"},
                {"path": "same.md"},
                *({"path": f"note-{i}.md"} for i in range(20)),
                {"path": None},
            ]
        }
    )

    summary = spec.summarize({"query": "q"}, result_text)

    assert len(summary.paths) == len(set(summary.paths)), "paths must be deduped"
    assert len(summary.paths) <= MAX_SUMMARY_PATHS


def test_list_tasks_summary_reports_a_count(settings: Settings) -> None:
    tools = build_vault_tools(settings, FakeIndex())
    spec = tool(tools, "list_tasks")
    result_text = json.dumps({"overdue": [{"text": "a"}], "today": [{"text": "b"}], "week": []})

    summary = spec.summarize({}, result_text)

    assert summary.label == "list_tasks"
    assert summary.detail == "2 tasks"


def test_build_vault_tools_still_build_with_summarize_set(settings: Settings) -> None:
    tools = build_vault_tools(settings, FakeIndex())
    assert all(spec.summarize is not None for spec in tools)


def test_the_system_prompt_no_longer_mandates_a_canned_refusal() -> None:
    """The verbatim "That's not in your notes." string was rule 3 until the
    retrieval floor made an empty result honest on its own. Pinning its absence
    keeps a well-meaning edit from reintroducing a scripted answer."""
    prompt = _load_system_prompt(Taxonomy())

    assert "That's not in your notes." not in prompt
    assert "Answer ONLY from tool results" not in prompt
