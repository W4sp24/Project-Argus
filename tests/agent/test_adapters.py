"""Tests for the OpenAI-compatible adapter's hand-run tool-calling loop.

Every case runs against ``httpx.MockTransport`` — no network, no Ollama, no
provider account — so the loop's real failure modes (fragmented tool calls,
multi-turn dispatch, malformed arguments, turn exhaustion) are exercised
deterministically in CI.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.agent.adapters import (
    AgentError,
    TextDelta,
    ToolFinished,
    ToolSpec,
    ToolStarted,
    ToolSummary,
    UsageReported,
    json_schema,
    text_result,
)
from backend.agent.openai_compat import (
    OpenAICompatAdapter,
    chat_completions_url,
    list_models,
    normalize_base_url,
    parse_tool_arguments,
)

ENDPOINT = "http://localhost:11434/v1"


# --- helpers ----------------------------------------------------------------


def sse(*chunks: dict) -> bytes:
    """Encode chunks as an SSE body, including keep-alives and the [DONE] tail."""
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    lines.append("\n")  # keep-alive: must be ignored, not crash the parser
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def tool_chunk(index: int, *, call_id: str = "", name: str = "", arguments: str = "") -> dict:
    """One streamed tool_calls fragment, exactly as providers emit them."""
    fragment: dict = {"index": index}
    if call_id:
        fragment["id"] = call_id
    function = {}
    if name:
        function["name"] = name
    if arguments:
        function["arguments"] = arguments
    if function:
        fragment["function"] = function
    return {"choices": [{"delta": {"tool_calls": [fragment]}}]}


def usage_chunk(prompt: int, completion: int) -> dict:
    return {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


def adapter_for(turns: list[bytes], *, record: list[dict] | None = None) -> OpenAICompatAdapter:
    """An adapter whose transport replays one prepared SSE body per turn."""
    remaining = list(turns)

    def handle(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(json.loads(request.content))
        body = remaining.pop(0) if remaining else sse(text_chunk("done"))
        return httpx.Response(200, content=body)

    return OpenAICompatAdapter(
        model="llama3.1", endpoint=ENDPOINT, transport=httpx.MockTransport(handle)
    )


def spy_tool(name: str = "search_vault", reply: str = "found it") -> tuple[ToolSpec, list[dict]]:
    """A ToolSpec that records the arguments it was called with."""
    seen: list[dict] = []

    async def handler(args: dict) -> dict:
        seen.append(args)
        return text_result(reply)

    spec = ToolSpec(
        name=name,
        description="search the vault",
        parameters=json_schema({"query": {"type": "string"}}),
        handler=handler,
    )
    return spec, seen


async def collect(adapter: OpenAICompatAdapter, tools: list[ToolSpec], max_turns: int = 8) -> list:
    return [
        event
        async for event in adapter.run(
            system_prompt="be helpful",
            user_message="what did I write about Dijkstra?",
            tools=tools,
            max_turns=max_turns,
        )
    ]


def texts(events: list) -> str:
    return "".join(event.text for event in events if isinstance(event, TextDelta))


# --- url handling -----------------------------------------------------------


def test_normalize_base_url_fills_in_missing_version_path() -> None:
    # The single most common non-developer mistake: pasting the bare origin.
    assert normalize_base_url("http://localhost:11434") == "http://localhost:11434/v1"
    assert normalize_base_url("http://localhost:11434/") == "http://localhost:11434/v1"
    assert normalize_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    assert normalize_base_url("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"


def test_normalize_base_url_rejects_non_urls() -> None:
    with pytest.raises(AgentError):
        normalize_base_url("not-a-url")


def test_chat_completions_url_is_idempotent() -> None:
    assert chat_completions_url(ENDPOINT) == "http://localhost:11434/v1/chat/completions"
    assert (
        chat_completions_url("http://localhost:11434/v1/chat/completions")
        == "http://localhost:11434/v1/chat/completions"
    )


# --- the loop ---------------------------------------------------------------


@pytest.mark.anyio
async def test_streams_text_when_no_tool_is_called() -> None:
    adapter = adapter_for([sse(text_chunk("Dijkstra "), text_chunk("finds paths"))])
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert texts(events) == "Dijkstra finds paths"
    assert seen == [], "no tool call means no handler invocation"
    assert sum(isinstance(e, UsageReported) for e in events) == 1


@pytest.mark.anyio
async def test_single_tool_call_dispatches_then_answers() -> None:
    sent: list[dict] = []
    adapter = adapter_for(
        [
            sse(
                tool_chunk(0, call_id="c1", name="search_vault"),
                tool_chunk(0, arguments='{"query":'),
                tool_chunk(0, arguments='"dijkstra"}'),
            ),
            sse(text_chunk("You wrote about it in algorithms.md")),
        ],
        record=sent,
    )
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert seen == [{"query": "dijkstra"}], "arguments reassembled across chunks"
    assert texts(events) == "You wrote about it in algorithms.md"
    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert [e.name for e in started] == ["search_vault"]
    assert [e.name for e in finished] == ["search_vault"]
    assert started[0].call_id == finished[0].call_id == "c1", "chip pairing needs a shared id"
    assert started[0].args == {"query": "dijkstra"}
    assert finished[0].summary == ToolSummary(label="search_vault", ok=True)

    # The second request must carry the assistant tool_calls turn and its result,
    # correlated by tool_call_id — this is what the SDK does internally.
    follow_up = sent[1]["messages"]
    assert follow_up[-2]["tool_calls"][0]["id"] == "c1"
    assert follow_up[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "search_vault",
        "content": "found it",
    }


@pytest.mark.anyio
async def test_parallel_tool_calls_accumulate_by_index_not_arrival_order() -> None:
    """Fragments interleave across indices; only `index` correlates them."""
    adapter = adapter_for(
        [
            sse(
                tool_chunk(0, call_id="a", name="search_vault"),
                tool_chunk(1, call_id="b", name="read_note"),
                tool_chunk(0, arguments='{"query":"x"}'),
                tool_chunk(1, arguments='{"path":"n.md"}'),
            ),
            sse(text_chunk("both done")),
        ]
    )
    search, search_seen = spy_tool("search_vault", "hits")
    read, read_seen = spy_tool("read_note", "contents")

    events = await collect(adapter, [search, read])

    assert search_seen == [{"query": "x"}]
    assert read_seen == [{"path": "n.md"}]
    assert [e.name for e in events if isinstance(e, ToolStarted)] == ["search_vault", "read_note"]
    assert [e.name for e in events if isinstance(e, ToolFinished)] == ["search_vault", "read_note"]
    assert texts(events) == "both done"


@pytest.mark.anyio
async def test_multi_turn_tool_use() -> None:
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="search_vault", arguments='{"query":"a"}')),
            sse(tool_chunk(0, call_id="c2", name="search_vault", arguments='{"query":"b"}')),
            sse(text_chunk("finally an answer")),
        ]
    )
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert seen == [{"query": "a"}, {"query": "b"}]
    assert texts(events) == "finally an answer"


@pytest.mark.anyio
async def test_max_turns_exhaustion_stops_the_loop() -> None:
    """A model that only ever calls tools must not loop forever."""
    forever = sse(tool_chunk(0, call_id="c", name="search_vault", arguments="{}"))
    adapter = adapter_for([forever, forever, forever, forever, forever])
    spec, seen = spy_tool()

    events = await collect(adapter, [spec], max_turns=3)

    assert len(seen) == 3, "exactly max_turns dispatches, then the loop ends"
    assert sum(isinstance(e, UsageReported) for e in events) == 1


@pytest.mark.anyio
async def test_malformed_tool_arguments_reach_the_handler_as_empty_dict() -> None:
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="search_vault", arguments="{not json")),
            sse(text_chunk("recovered")),
        ]
    )
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert seen == [{}], "the handler validates and replies; the turn is not aborted"
    assert texts(events) == "recovered"


@pytest.mark.anyio
async def test_unknown_tool_returns_an_error_the_model_can_read() -> None:
    sent: list[dict] = []
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="delete_everything", arguments="{}")),
            sse(text_chunk("sorry")),
        ],
        record=sent,
    )
    spec, seen = spy_tool()

    await collect(adapter, [spec])

    assert seen == []
    assert "unknown tool" in sent[1]["messages"][-1]["content"]


@pytest.mark.anyio
async def test_handler_exception_becomes_tool_text_not_a_crash() -> None:
    sent: list[dict] = []
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="search_vault", arguments="{}")),
            sse(text_chunk("carried on")),
        ],
        record=sent,
    )

    async def explode(_args: dict) -> dict:
        raise RuntimeError("index is cold")

    spec = ToolSpec("search_vault", "search", json_schema({}), explode)

    events = await collect(adapter, [spec])

    assert "index is cold" in sent[1]["messages"][-1]["content"]
    assert texts(events) == "carried on"


@pytest.mark.anyio
async def test_error_dispatch_produces_a_failed_summary() -> None:
    """No summarizer means the fallback reads "error: ..." to set ok=False."""
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="delete_everything", arguments="{}")),
            sse(text_chunk("sorry")),
        ]
    )
    spec, _ = spy_tool()

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary.ok is False


@pytest.mark.anyio
async def test_synthesized_call_id_reaches_both_start_and_finish() -> None:
    """A provider that omits ids still lets a frontend pair the two events."""
    adapter = adapter_for(
        [
            sse(tool_chunk(0, name="search_vault", arguments="{}")),
            sse(text_chunk("done")),
        ]
    )
    spec, _ = spy_tool()

    events = await collect(adapter, [spec])

    started = next(e for e in events if isinstance(e, ToolStarted))
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert started.call_id == finished.call_id == "call_0"


@pytest.mark.anyio
async def test_tool_spec_summarize_shapes_the_finished_summary() -> None:
    def summarize(args: dict, result_text: str) -> ToolSummary:
        return ToolSummary(
            label=f"searched for {args['query']}", detail=result_text, paths=("algorithms.md",)
        )

    async def handler(_args: dict) -> dict:
        return text_result("found it")

    spec = ToolSpec(
        name="search_vault",
        description="search the vault",
        parameters=json_schema({"query": {"type": "string"}}),
        handler=handler,
        summarize=summarize,
    )
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="search_vault", arguments='{"query":"x"}')),
            sse(text_chunk("done")),
        ]
    )

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == ToolSummary(
        label="searched for x", detail="found it", paths=("algorithms.md",)
    )


@pytest.mark.anyio
async def test_summarize_that_raises_falls_back_without_breaking_the_run() -> None:
    def summarize(_args: dict, _result_text: str) -> ToolSummary:
        raise RuntimeError("summarizer bug")

    async def handler(_args: dict) -> dict:
        return text_result("found it")

    spec = ToolSpec(
        name="search_vault",
        description="search the vault",
        parameters=json_schema({}),
        handler=handler,
        summarize=summarize,
    )
    adapter = adapter_for(
        [
            sse(tool_chunk(0, call_id="c1", name="search_vault", arguments="{}")),
            sse(text_chunk("carried on")),
        ]
    )

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == ToolSummary(label="search_vault")
    assert texts(events) == "carried on"


@pytest.mark.anyio
async def test_usage_is_recorded_when_the_provider_reports_it() -> None:
    adapter = adapter_for([sse(text_chunk("hi"), usage_chunk(120, 30))])

    events = await collect(adapter, [])

    usage = next(e for e in events if isinstance(e, UsageReported))
    assert usage.usage["input_tokens"] == 120
    assert usage.usage["output_tokens"] == 30


@pytest.mark.anyio
async def test_toolless_run_sends_no_tools_field() -> None:
    sent: list[dict] = []
    adapter = adapter_for([sse(text_chunk("plain answer"))], record=sent)

    events = await collect(adapter, [])

    assert "tools" not in sent[0]
    assert texts(events) == "plain answer"


@pytest.mark.anyio
async def test_system_prompt_is_sent_first() -> None:
    sent: list[dict] = []
    adapter = adapter_for([sse(text_chunk("ok"))], record=sent)

    await collect(adapter, [])

    assert sent[0]["messages"][0] == {"role": "system", "content": "be helpful"}


# --- errors -----------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, {"error": {"message": "model not found"}}, "is the model 'llama3.1' pulled"),
        (401, {"error": {"message": "bad key"}}, "check the API key"),
        (500, {"error": "boom"}, "boom"),
    ],
)
async def test_http_errors_surface_actionable_messages(status, body, expected) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    adapter = OpenAICompatAdapter(
        model="llama3.1", endpoint=ENDPOINT, transport=httpx.MockTransport(handle)
    )

    with pytest.raises(AgentError, match=expected):
        await collect(adapter, [])


@pytest.mark.anyio
async def test_api_key_rides_along_as_a_bearer_token() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, content=sse(text_chunk("ok")))

    adapter = OpenAICompatAdapter(
        model="llama-3.3-70b",
        endpoint="https://api.groq.com/openai/v1",
        api_key="secret-key",
        transport=httpx.MockTransport(handle),
    )

    await collect(adapter, [])

    assert seen == ["Bearer secret-key"]


# --- model listing ----------------------------------------------------------


@pytest.mark.anyio
async def test_list_models_returns_sorted_ids() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/models")
        return httpx.Response(200, json={"data": [{"id": "qwen2.5"}, {"id": "llama3.1"}]})

    assert await list_models(ENDPOINT, transport=httpx.MockTransport(handle)) == [
        "llama3.1",
        "qwen2.5",
    ]


def test_parse_tool_arguments_tolerates_empty_and_non_object_json() -> None:
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments("   ") == {}
    assert parse_tool_arguments("[1,2]") == {}
    assert parse_tool_arguments("garbage") == {}
