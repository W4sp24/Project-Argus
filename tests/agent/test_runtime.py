"""Tests for the chat runtime's vault tools.

Focused on the index-warm gate: `search_vault` must not touch the chroma
client while `ChatAgent.warm()` is still loading the embedding model on its
background thread.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from backend.agent.adapters import ToolSummary, resolve_run_target
from backend.agent.runtime import (
    MODEL,
    ChatAgent,
    _load_system_prompt,
    _summarize_list_notes,
    acknowledge_tool_steps,
    build_vault_tools,
)
from backend.core.config import Settings
from backend.core.model_registry import save_model_prefs, save_user_models
from backend.core.taxonomy import Taxonomy

BROKEN_HEADER_NOTE = """---
tags: [unclosed
---

still mine
"""


NO_AI_NOTE = """---
tags: [no-ai]
---

private thoughts
"""


class FakeIndex:
    """Stands in for VaultIndex, recording when it was queried."""

    def __init__(self) -> None:
        self.queried_at: list[float] = []

    def query(self, *_args, **_kwargs):
        self.queried_at.append(time.monotonic())
        return {}


class NeverWarms(threading.Event):
    """An event that is never set, and that reports being waited on.

    The property under test is structural -- a handler either reaches the warm
    gate or it does not -- and a stopwatch was only ever a proxy for it. The
    proxy measures the machine: on a loaded CI runner this handler took 1.047s
    against a 1.0s budget and failed a test about gating with a number about
    scheduling. Recording the call answers the real question, and returning
    immediately means a handler that *is* gated fails on the flag rather than
    stalling the suite for WARM_TIMEOUT_SECONDS.
    """

    def __init__(self) -> None:
        super().__init__()
        self.waited = False

    def wait(self, timeout: float | None = None) -> bool:
        self.waited = True
        return False


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

    # Headroom, not a performance budget: the only way to fail this is to wait
    # on an event nobody passed, and that costs WARM_TIMEOUT_SECONDS (90s). A
    # tighter number measures how loaded the runner is -- which is how the
    # read_note test below went red at 1.047s.
    assert time.monotonic() - started < 30.0


@pytest.mark.anyio
async def test_read_note_is_not_gated(settings: Settings) -> None:
    """read_note never touches the index, so it must not wait on the warm."""
    ready = NeverWarms()
    tools = build_vault_tools(settings, FakeIndex(), ready=ready)

    result = await tool(tools, "read_note").handler({"path": "50-Reference/algorithms.md"})

    assert not ready.waited, "read_note waited on the index warm"
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
    assert names == {"search_vault", "read_note", "list_notes", "list_tasks", "list_events"}
    assert not any(name.startswith("propose_") for name in names)
    # list_events reads; there is deliberately no create_event beside it.
    # A write the model performs directly would bypass the approval gate
    # every other vault mutation goes through, and scheduling already has
    # a write path: propose_schedule raises a suggestion to approve.
    assert not any(name.startswith(("create_", "update_", "delete_")) for name in names)


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
    monkeypatch.setattr(
        "backend.agent.runtime.resolve_run_target", lambda *a, **k: (adapter, "test-model")
    )
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
    monkeypatch.setattr(
        "backend.agent.runtime.resolve_run_target", lambda *a, **k: (adapter, "test-model")
    )
    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", lambda *a, **k: [])

    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    assert [chunk async for chunk in agent.stream_chat("hi")] == ["a long ", "expensive answer"]

    assert _usage_rows(settings) == [("chat", 400, 800)]


@pytest.mark.anyio
async def test_the_rerank_setting_reaches_retrieval(settings: Settings, monkeypatch) -> None:
    """`ARGUS_RAG_RERANK=1` used to do nothing at all.

    `retrieve_result` takes a `rerank` flag, but both callers go through the
    `retrieve` shim and the shim dropped it — so the setting was unreachable
    while rerank.py's docstring claimed it gated the module.
    """
    seen: list[dict] = []

    def fake_retrieve(*_args, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr("backend.rag.retrieve.retrieve", fake_retrieve)
    # Settings is frozen, so this is a replacement rather than a mutation.
    reranking = dataclasses.replace(settings, rerank_enabled=True)

    tools = build_vault_tools(reranking, FakeIndex())
    await tool(tools, "search_vault").handler({"query": "dijkstra"})

    assert seen[0]["rerank"] is True


@pytest.mark.anyio
async def test_a_missing_argument_is_explained_not_raised(settings: Settings) -> None:
    """`str(args["query"])` used to raise KeyError, and the model read the whole
    of `error: 'query'` — which names neither the tool nor the shape it wanted,
    so the next attempt was another guess and the turn budget drained."""
    tools = build_vault_tools(settings, FakeIndex())

    searched = json.dumps(await tool(tools, "search_vault").handler({}))
    read = json.dumps(await tool(tools, "read_note").handler({"path": "  "}))

    assert "search_vault needs" in searched and "dijkstra" in searched
    assert "read_note needs" in read and "vault-relative" in read


@pytest.mark.anyio
async def test_list_notes_browses_a_folder(settings: Settings) -> None:
    """The escape hatch for a search that came back thin.

    Before this the read surface was search / read-one / list-tasks, so a model
    that could not phrase a query the embeddings liked had no way to discover
    that a note existed at all.
    """
    course = settings.vault_path / "15-Courses" / "CS201"
    course.mkdir(parents=True)
    (course / "Lecture-03-Graphs.md").write_text("dijkstra", encoding="utf-8")
    (course / "Exam-1-review.md").write_text("revision", encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_notes").handler({"folder": "15-Courses/CS201"})
    paths = json.loads(json.loads(json.dumps(listed))["content"][0]["text"])["paths"]

    assert sorted(paths) == [
        "15-Courses/CS201/Exam-1-review.md",
        "15-Courses/CS201/Lecture-03-Graphs.md",
    ]
    assert "50-Reference/algorithms.md" not in paths, "the folder filter is not advisory"


@pytest.mark.anyio
async def test_list_notes_matches_on_the_filename(settings: Settings) -> None:
    """The case semantic search is worst at: the word is in the name, not the
    body."""
    (settings.vault_path / "50-Reference" / "Kruskal-notes.md").write_text("x", encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_notes").handler({"name_contains": "kruskal"})
    paths = json.loads(listed["content"][0]["text"])["paths"]

    assert paths == ["50-Reference/Kruskal-notes.md"]


@pytest.mark.anyio
async def test_list_notes_never_lists_a_no_ai_note(settings: Settings) -> None:
    """I3 applies to a listing as much as to a read: a path is content."""
    (settings.vault_path / "50-Reference" / "diary.md").write_text(NO_AI_NOTE, encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_notes").handler({})
    paths = json.loads(listed["content"][0]["text"])["paths"]

    assert "50-Reference/diary.md" not in paths
    assert "50-Reference/algorithms.md" in paths, "only the tagged note is withheld"


@pytest.mark.anyio
async def test_a_broken_frontmatter_header_does_not_hide_a_note(settings: Settings) -> None:
    """Failing closed on a parse error would make a note with a typo in its
    YAML silently vanish from browse — the very complaint this tool answers."""
    (settings.vault_path / "50-Reference" / "wonky.md").write_text(
        BROKEN_HEADER_NOTE, encoding="utf-8"
    )

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_notes").handler({})
    paths = json.loads(listed["content"][0]["text"])["paths"]

    assert "50-Reference/wonky.md" in paths


@pytest.mark.anyio
async def test_list_notes_caps_and_says_that_it_did(settings: Settings, monkeypatch) -> None:
    monkeypatch.setattr("backend.agent.runtime.MAX_LIST_PATHS", 2)
    for i in range(5):
        (settings.vault_path / "50-Reference" / f"note-{i}.md").write_text("x", encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_notes").handler({})
    payload = json.loads(listed["content"][0]["text"])

    assert len(payload["paths"]) == 2
    assert "narrow it" in payload["note"], "a silent truncation reads as a complete listing"


def test_a_listed_path_is_not_a_citation() -> None:
    """Citations come from the trace, so a summary carrying paths manufactures
    them. The model has seen a filename here, not any content."""
    summary = _summarize_list_notes(
        {"folder": "15-Courses"}, json.dumps({"paths": ["15-Courses/a.md", "15-Courses/b.md"]})
    )

    assert summary.paths == ()
    assert summary.detail == "2 notes in 15-Courses"


@pytest.mark.anyio
async def test_read_note_refuses_a_no_ai_note_outside_the_private_folder(
    settings: Settings,
) -> None:
    """I3's tag half, which `is_indexable` never checked.

    Indexing excludes a `#no-ai` note, so `search_vault` can never surface its
    path — but `read_note` gated on the directory-and-suffix check alone, so a
    model that guessed the path, or was handed it, read the file anyway. The
    chat prompt's assurance that "the tools already exclude them" was true of
    search and false here.
    """
    note = settings.vault_path / "50-Reference" / "diary.md"
    note.write_text(NO_AI_NOTE, encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    result = await tool(tools, "read_note").handler({"path": "50-Reference/diary.md"})

    assert "not readable" in json.dumps(result)
    assert "private thoughts" not in json.dumps(result)


@pytest.mark.anyio
async def test_read_note_cannot_walk_out_of_the_vault(settings: Settings, tmp_path) -> None:
    """`is_indexable` filters directory names and suffixes and has no opinion
    on `..`, so a relative path was resolved against the vault raw. The model
    picks this argument, so containment is checked where it is used."""
    outside = tmp_path / "outside.md"
    outside.write_text("not yours", encoding="utf-8")

    tools = build_vault_tools(settings, FakeIndex())
    result = await tool(tools, "read_note").handler({"path": "../outside.md"})

    assert "not readable" in json.dumps(result)
    assert "not yours" not in json.dumps(result)


def test_a_model_less_turn_is_labelled_with_the_model_that_actually_ran(
    settings: Settings,
) -> None:
    """Chat used to answer `claude-opus-4-8` for every model-less turn.

    `resolve_adapter` resolves a falsy model through `settings.default_model`,
    but chat's own `_resolve_model` returned the hardcoded Claude Code id — so
    on a machine defaulting to Ollama or DeepSeek the adapter ran that model
    while every usage and audit row named Anthropic's. One resolution now
    answers both questions, and the label is whatever adapter came back.
    """
    settings.models_file.parent.mkdir(parents=True, exist_ok=True)
    save_user_models(
        settings.models_file,
        [
            {
                "name": "local-llama",
                "provider": "openai-compat",
                "endpoint": "http://127.0.0.1:11434/v1",
                "model_id": "llama3.1:8b",
            }
        ],
    )
    save_model_prefs(settings.model_prefs_file, {"default": "local-llama"})

    adapter, label = resolve_run_target(settings, None, fallback_model=MODEL)

    assert adapter.model == "llama3.1:8b"
    assert label == "llama3.1:8b", "the label followed the fallback, not the running model"

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
        "backend.agent.runtime.resolve_run_target",
        lambda *a, **k: (_AbandonableAdapter(), "test-model"),
    )

    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    async for _ in agent.stream_chat("what's on the exam", course="CS201"):
        pass

    assert captured.get("course") == "CS201"


def test_the_system_prompt_names_every_tool_the_belt_actually_has() -> None:
    """A tool the prompt never mentions is one a weaker model will not reach
    for. `list_notes` in particular exists to be the escalation step after a
    thin search, which only works if the prompt says so."""
    prompt = _load_system_prompt(Taxonomy())
    names = {spec.name for spec in build_vault_tools(Settings(_vault_path=Path(".")), FakeIndex())}

    for name in names:
        assert f"`{name}(" in prompt or f"`{name}`" in prompt, f"{name} is undocumented"


def test_the_system_prompt_covers_the_automation_tools_too() -> None:
    """`stream_chat` appends `build_automation_tools` to the belt, and those are
    the only tools in chat that *change* anything. The test above cannot see
    them — it builds `build_vault_tools` alone — so the write-capable half of
    the belt was structurally outside the prompt's coverage, which is part of
    how an automation could run without the reply ever mentioning it."""
    prompt = _load_system_prompt(Taxonomy())

    assert "`run_automation_*`" in prompt


def test_the_system_prompt_forbids_writing_tool_calls_as_prose() -> None:
    """A local model that prints its call is sieved out server-side
    (backend/agent/text_tool_calls.py); telling it not to is the cheaper half of
    the same fix."""
    prompt = _load_system_prompt(Taxonomy())

    assert "Never write a tool call yourself" in prompt


def test_the_system_prompt_requires_actions_to_be_reported() -> None:
    """The reported failure: a note was created and the reply never said so."""
    prompt = _load_system_prompt(Taxonomy())

    assert "Report every action you take" in prompt


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


# --- acknowledging what a turn actually did ----------------------------------
#
# Reported failure: "a request to plan for tomorrow created an Obsidian file but
# did not reflect that result in the conversation." A turn that runs a tool and
# then produces no text renders as a tool chip followed by silence, and the user
# has no way to learn that anything happened.


class _SilentToolAdapter:
    """Runs a tool and then says nothing — the shape the bug report describes.

    This is not contrived: `openai_compat` forces its last turn to text, but a
    model that spends that turn on a tool call exits the loop with the result
    unread, and the assistant turn is banked with steps and an empty body.
    """

    model = "fake-model"

    def __init__(self, summary: ToolSummary) -> None:
        self._summary = summary

    async def run(self, **_kwargs):
        from backend.agent.adapters import ToolFinished, ToolStarted, UsageReported

        yield ToolStarted(call_id="c1", name=self._summary.label, args={})
        yield ToolFinished(call_id="c1", name=self._summary.label, summary=self._summary)
        yield UsageReported(input_tokens=1, output_tokens=1)


async def _run_silent(settings: Settings, monkeypatch, summary: ToolSummary) -> str:
    monkeypatch.setattr(
        "backend.agent.runtime.resolve_run_target",
        lambda *a, **k: (_SilentToolAdapter(summary), "test-model"),
    )
    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", lambda *a, **k: [])
    monkeypatch.setattr(
        "backend.features.automations.tools.build_automation_tools", lambda *a, **k: []
    )
    agent = ChatAgent(settings)
    agent._index = FakeIndex()
    return "".join(
        [event async for event in agent.stream_chat("plan tomorrow") if isinstance(event, str)]
    )


@pytest.mark.anyio
async def test_a_turn_that_runs_a_tool_and_says_nothing_still_reports_it(
    settings: Settings, monkeypatch
) -> None:
    written = ToolSummary(
        label="run_automation_plan",
        detail="Plan tomorrow (success)",
        paths=("10-Daily/2026-08-22.md",),
    )

    text = await _run_silent(settings, monkeypatch, written)

    assert "Plan tomorrow" in text, "the action went unreported"
    assert "10-Daily/2026-08-22.md" in text, "the file it wrote was never named"


@pytest.mark.anyio
async def test_a_turn_that_answers_is_left_alone(settings: Settings, monkeypatch) -> None:
    """The acknowledgement must never append itself to a real answer."""
    monkeypatch.setattr(
        "backend.agent.runtime.resolve_run_target",
        lambda *a, **k: (_AbandonableAdapter(), "test-model"),
    )
    monkeypatch.setattr("backend.agent.runtime.build_vault_tools", lambda *a, **k: [])
    monkeypatch.setattr(
        "backend.features.automations.tools.build_automation_tools", lambda *a, **k: []
    )
    agent = ChatAgent(settings)
    agent._index = FakeIndex()

    text = "".join(
        [event async for event in agent.stream_chat("hello") if isinstance(event, str)]
    )

    assert text == "a long expensive answer"


def test_a_successful_write_is_not_reported_as_a_failure() -> None:
    message = acknowledge_tool_steps(
        [ToolSummary(label="run_automation_x", detail="Meeting prep (success)")]
    )

    assert "ran the automation Meeting prep" in message
    assert "could not" not in message


def test_a_failed_write_says_so_without_stuttering() -> None:
    message = acknowledge_tool_steps(
        [ToolSummary(label="run_automation_x", detail="Sync (failed)", ok=False)]
    )

    assert "without success" in message
    # The summarizer already puts the status in `detail`; repeating it reads as
    # "tried to run Sync (failed), but it failed".
    assert "(failed)" not in message


def test_repeated_identical_steps_read_as_one() -> None:
    same = ToolSummary(label="search_vault", detail="'Dijkstra'")

    message = acknowledge_tool_steps([same, same, same])

    assert message.count("searched your vault") == 1


def test_a_turn_with_no_steps_gets_no_acknowledgement() -> None:
    assert acknowledge_tool_steps([]) == ""


@pytest.mark.anyio
async def test_search_vault_is_pinned_to_the_selected_sources(
    settings: Settings, monkeypatch
) -> None:
    """The Course Hub SOURCES ticks are a fixed scope, exactly as `course` is:
    the user narrowed this conversation, and the model does not get to widen
    it back out."""
    seen: list[list[str] | None] = []
    monkeypatch.setattr(
        "backend.rag.retrieve.retrieve", lambda *a, **k: seen.append(k.get("paths")) or []
    )

    tools = build_vault_tools(
        settings, FakeIndex(), course="CS201", sources=["15-Courses/CS201/materials/wk1.pdf"]
    )
    await tool(tools, "search_vault").handler({"query": "midterm"})

    assert seen == [["15-Courses/CS201/materials/wk1.pdf"]]


@pytest.mark.anyio
async def test_search_vault_leaves_paths_unset_when_nothing_is_pinned(
    settings: Settings, monkeypatch
) -> None:
    """Global chat must keep searching the whole vault."""
    seen: list[list[str] | None] = []
    monkeypatch.setattr(
        "backend.rag.retrieve.retrieve", lambda *a, **k: seen.append(k.get("paths")) or []
    )

    tools = build_vault_tools(settings, FakeIndex())
    await tool(tools, "search_vault").handler({"query": "midterm"})

    assert seen == [None]


@pytest.mark.anyio
async def test_search_vault_says_so_when_every_source_is_unticked(
    settings: Settings, monkeypatch
) -> None:
    """An empty selection must not silently fall back to the whole vault, and
    must not look like "the vault has nothing on this" either — the model
    would report the material absent when it is merely deselected."""
    called = {"n": 0}
    monkeypatch.setattr(
        "backend.rag.retrieve.retrieve", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )

    tools = build_vault_tools(settings, FakeIndex(), course="CS201", sources=[])
    result = await tool(tools, "search_vault").handler({"query": "midterm"})

    assert called["n"] == 0, "nothing is selected, so there is nothing to search"
    assert "no sources are selected" in str(result)


def test_the_tool_description_admits_a_narrowed_scope(settings: Settings) -> None:
    """A model that believes it can see the whole course will state that
    something is missing when it was only unticked."""
    scoped = tool(
        build_vault_tools(settings, FakeIndex(), sources=["a.md", "b.md"]), "search_vault"
    )
    assert "2 specific file(s)" in scoped.description

    unscoped = tool(build_vault_tools(settings, FakeIndex()), "search_vault")
    assert "specific file(s)" not in unscoped.description


# --- Calendar tools ----------------------------------------------------------


@pytest.mark.anyio
async def test_list_events_sees_the_local_calendar(settings: Settings) -> None:
    """The agent could read tasks but was blind to the calendar entirely.

    Read-only on purpose: placing an event still goes through
    propose_schedule and the approval gate, like every other write.
    """
    from backend.core.db import connect, init_schema
    from backend.features.calendar import store as calendar_store

    conn = connect(settings.db_path)
    init_schema(conn)
    calendar_store.ensure_default_calendar(conn)
    calendar_store.upsert_event(
        conn,
        calendar_store.DEFAULT_CALENDAR_ID,
        title="Study block",
        start="2026-09-01T15:00:00",
        end="2026-09-01T17:00:00",
    )
    conn.close()

    tools = build_vault_tools(settings, FakeIndex())
    listed = await tool(tools, "list_events").handler(
        {"start": "2026-09-01", "end": "2026-09-02"}
    )

    payload = json.loads(listed["content"][0]["text"])
    assert [event["title"] for event in payload["events"]] == ["Study block"]


@pytest.mark.anyio
async def test_list_events_survives_a_date_the_model_invented(settings: Settings) -> None:
    """A tool that rejects a slightly wrong argument becomes a tool nobody calls.

    Models pass "next Tuesday" and empty strings. Falling back to a sensible
    window beats an error the model then has to recover from.
    """
    tools = build_vault_tools(settings, FakeIndex())

    listed = await tool(tools, "list_events").handler({"start": "next Tuesday"})

    assert json.loads(listed["content"][0]["text"])["events"] == []
