"""Tests for the Anthropic Messages API adapter (Claude without Claude Code).

Mocked transport throughout: the Messages API's shape differs from OpenAI's in
ways that are easy to get subtly wrong — ``input_schema`` instead of nested
``function.parameters``, tool arguments streaming as ``input_json_delta``
against a block index, and results going back inside a **user** message — so
each of those is pinned by a test.
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
from backend.agent.anthropic_api import AnthropicAPIAdapter, list_models, to_anthropic_tools


def sse(*events: dict) -> bytes:
    """Encode Messages-API stream events, including the `event:` name lines."""
    parts = []
    for event in events:
        parts.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(parts).encode()


def text_delta(text: str) -> dict:
    return {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }


def tool_start(index: int, call_id: str, name: str) -> dict:
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
    }


def tool_delta(index: int, partial: str) -> dict:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial},
    }


def adapter_for(turns: list[bytes], *, record: list[dict] | None = None) -> AnthropicAPIAdapter:
    remaining = list(turns)

    def handle(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(json.loads(request.content))
        body = remaining.pop(0) if remaining else sse(text_delta("done"))
        return httpx.Response(200, content=body)

    return AnthropicAPIAdapter(
        model="claude-sonnet-5", api_key="sk-test", transport=httpx.MockTransport(handle)
    )


def spy_tool(name: str = "search_vault") -> tuple[ToolSpec, list[dict]]:
    seen: list[dict] = []

    async def handler(args: dict) -> dict:
        seen.append(args)
        return text_result("found it")

    return (
        ToolSpec(name, "search the vault", json_schema({"query": {"type": "string"}}), handler),
        seen,
    )


async def collect(adapter: AnthropicAPIAdapter, tools: list[ToolSpec], max_turns: int = 8) -> list:
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


def test_tools_use_input_schema_not_openai_nesting() -> None:
    spec, _ = spy_tool()
    assert to_anthropic_tools([spec]) == [
        {
            "name": "search_vault",
            "description": "search the vault",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


@pytest.mark.anyio
async def test_streams_text_when_no_tool_is_called() -> None:
    adapter = adapter_for([sse(text_delta("Dijkstra "), text_delta("finds paths"))])
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert texts(events) == "Dijkstra finds paths"
    assert seen == []


@pytest.mark.anyio
async def test_tool_use_dispatches_and_results_go_back_as_a_user_message() -> None:
    sent: list[dict] = []
    adapter = adapter_for(
        [
            sse(
                tool_start(0, "toolu_1", "search_vault"),
                tool_delta(0, '{"query":'),
                tool_delta(0, '"dijkstra"}'),
            ),
            sse(text_delta("It's in algorithms.md")),
        ],
        record=sent,
    )
    spec, seen = spy_tool()

    events = await collect(adapter, [spec])

    assert seen == [{"query": "dijkstra"}], "input_json_delta fragments reassembled"
    assert texts(events) == "It's in algorithms.md"
    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert [e.name for e in started] == ["search_vault"]
    assert [e.name for e in finished] == ["search_vault"]
    assert started[0].call_id == finished[0].call_id == "toolu_1", "chip pairing needs shared id"
    assert started[0].args == {"query": "dijkstra"}
    assert finished[0].summary == ToolSummary(label="search_vault", ok=True)

    follow_up = sent[1]["messages"]
    assert follow_up[-2]["role"] == "assistant"
    assert follow_up[-2]["content"][-1]["type"] == "tool_use"
    # The Messages API has no tool role — results ride in a user message.
    assert follow_up[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "found it"}],
    }


@pytest.mark.anyio
async def test_parallel_tool_blocks_accumulate_by_index() -> None:
    adapter = adapter_for(
        [
            sse(
                tool_start(0, "t0", "search_vault"),
                tool_start(1, "t1", "read_note"),
                tool_delta(0, '{"query":"x"}'),
                tool_delta(1, '{"path":"n.md"}'),
            ),
            sse(text_delta("both done")),
        ]
    )
    search, search_seen = spy_tool("search_vault")
    read, read_seen = spy_tool("read_note")

    await collect(adapter, [search, read])

    assert search_seen == [{"query": "x"}]
    assert read_seen == [{"path": "n.md"}]


@pytest.mark.anyio
async def test_unknown_tool_produces_a_failed_summary() -> None:
    """No summarizer means the fallback reads "error: ..." to set ok=False."""
    adapter = adapter_for(
        [
            sse(tool_start(0, "t0", "delete_everything"), tool_delta(0, "{}")),
            sse(text_delta("sorry")),
        ]
    )
    spec, _ = spy_tool()

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary.ok is False


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
            sse(tool_start(0, "t0", "search_vault"), tool_delta(0, '{"query":"x"}')),
            sse(text_delta("done")),
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
            sse(tool_start(0, "t0", "search_vault"), tool_delta(0, "{}")),
            sse(text_delta("carried on")),
        ]
    )

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == ToolSummary(label="search_vault")
    assert texts(events) == "carried on"


@pytest.mark.anyio
async def test_max_turns_exhaustion_stops_the_loop() -> None:
    forever = sse(tool_start(0, "t", "search_vault"), tool_delta(0, "{}"))
    adapter = adapter_for([forever] * 5)
    spec, seen = spy_tool()

    await collect(adapter, [spec], max_turns=2)

    assert len(seen) == 2


@pytest.mark.anyio
async def test_usage_accumulates_across_message_start_and_delta() -> None:
    adapter = adapter_for(
        [
            sse(
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 200, "cache_read_input_tokens": 50}},
                },
                text_delta("hi"),
                {"type": "message_delta", "usage": {"output_tokens": 17}},
            )
        ]
    )

    events = await collect(adapter, [])

    usage = next(e for e in events if isinstance(e, UsageReported))
    assert usage.usage == {
        "input_tokens": 200,
        "output_tokens": 17,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 50,
    }


@pytest.mark.anyio
async def test_overlapping_usage_keys_are_not_double_counted() -> None:
    """The real shape of the API's events, which the disjoint fixture above hides.

    ``message_start`` always carries a small ``output_tokens`` alongside the
    input counters, and newer API versions repeat ``input_tokens`` and the cache
    figures on ``message_delta``. Summing every key of both events therefore
    over-reported output on every single message and doubled input outright —
    real money, wrong, on the user's dashboard.
    """
    adapter = adapter_for(
        [
            sse(
                {
                    "type": "message_start",
                    "message": {
                        "usage": {
                            "input_tokens": 200,
                            "output_tokens": 2,  # the API's initial estimate
                            "cache_read_input_tokens": 50,
                        }
                    },
                },
                text_delta("hi"),
                {
                    "type": "message_delta",
                    "usage": {
                        "input_tokens": 200,  # echoed, not additional
                        "output_tokens": 17,  # cumulative for the message
                        "cache_read_input_tokens": 50,
                    },
                },
            )
        ]
    )

    events = await collect(adapter, [])

    usage = next(e for e in events if isinstance(e, UsageReported))
    assert usage.usage == {
        "input_tokens": 200,  # not 400
        "output_tokens": 17,  # not 19
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 50,  # not 100
    }


@pytest.mark.anyio
async def test_usage_sums_across_turns_not_within_a_message() -> None:
    """Each tool-use turn is a separate billed request, so turns *do* add up."""
    spec, _ = spy_tool()
    adapter = adapter_for(
        [
            sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 100}}},
                tool_start(0, "call_1", "search_vault"),
                tool_delta(0, '{"query": "x"}'),
                {"type": "message_delta", "usage": {"input_tokens": 100, "output_tokens": 10}},
            ),
            sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 300}}},
                text_delta("answer"),
                {"type": "message_delta", "usage": {"input_tokens": 300, "output_tokens": 40}},
            ),
        ]
    )

    events = await collect(adapter, [spec], max_turns=3)

    usage = next(e for e in events if isinstance(e, UsageReported))
    assert usage.input_tokens == 400, "two separate requests, both billed"
    assert usage.output_tokens == 50


@pytest.mark.anyio
async def test_partial_usage_survives_an_abandoned_stream() -> None:
    """Closing the tab mid-answer must not lose tokens the provider already billed."""
    adapter = adapter_for(
        [
            sse(
                {"type": "message_start", "message": {"usage": {"input_tokens": 500}}},
                text_delta("a long answer that "),
                text_delta("the user walks away from"),
                {"type": "message_delta", "usage": {"output_tokens": 900}},
            )
        ]
    )

    stream = adapter.run(
        system_prompt="", user_message="explain everything", tools=[], max_turns=1
    )
    assert isinstance(await stream.__anext__(), TextDelta)  # first delta, then give up
    await stream.aclose()

    partial = adapter.partial_usage()
    assert partial is not None
    assert partial.input_tokens == 500, "message_start had already reported the input cost"


@pytest.mark.anyio
async def test_system_prompt_is_a_top_level_field_not_a_message() -> None:
    sent: list[dict] = []
    adapter = adapter_for([sse(text_delta("ok"))], record=sent)

    await collect(adapter, [])

    assert sent[0]["system"] == "be helpful"
    assert all(message["role"] != "system" for message in sent[0]["messages"])
    assert sent[0]["max_tokens"] > 0, "max_tokens is required by the Messages API"


@pytest.mark.anyio
async def test_api_key_travels_as_x_api_key_header() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        assert request.headers.get("anthropic-version")
        return httpx.Response(200, content=sse(text_delta("ok")))

    adapter = AnthropicAPIAdapter(
        model="claude-sonnet-5", api_key="sk-secret", transport=httpx.MockTransport(handle)
    )

    await collect(adapter, [])

    assert seen == ["sk-secret"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "check the Anthropic API key"), (429, "rate limit"), (500, "overloaded")],
)
async def test_http_errors_surface_actionable_messages(status, expected) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "overloaded"}})

    adapter = AnthropicAPIAdapter(
        model="claude-sonnet-5", api_key="sk", transport=httpx.MockTransport(handle)
    )

    with pytest.raises(AgentError, match=expected):
        await collect(adapter, [])


@pytest.mark.anyio
async def test_mid_stream_error_event_raises() -> None:
    adapter = adapter_for(
        [sse(text_delta("partial"), {"type": "error", "error": {"message": "overloaded_error"}})]
    )

    with pytest.raises(AgentError, match="overloaded_error"):
        await collect(adapter, [])


@pytest.mark.anyio
async def test_list_models_returns_sorted_ids() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "sk"
        return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-a"}]})

    assert await list_models("sk", transport=httpx.MockTransport(handle)) == [
        "claude-a",
        "claude-sonnet-5",
    ]
