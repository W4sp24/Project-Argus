"""Tests for the Gemini adapter.

Every case runs against ``httpx.MockTransport`` — no network, no key, no
quota. The ``record`` list is the outbound-request spy: most of what can go
wrong with this provider is the shape of the request, not the handling of the
response, because Gemini rejects a schema it does not recognise with a 400 for
the *whole* request rather than ignoring the offending tool.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest

from backend.agent.adapters import (
    AgentError,
    Message,
    Notice,
    TextDelta,
    ToolFinished,
    ToolSpec,
    ToolStarted,
    UsageReported,
    json_schema,
    text_result,
)
from backend.agent.gemini_api import GeminiAdapter, list_models, to_gemini_tools


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode()


def text_chunk(text: str) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def call_chunk(name: str, args: dict) -> dict:
    part = {"functionCall": {"name": name, "args": args}}
    return {"candidates": [{"content": {"role": "model", "parts": [part]}}]}


def usage_chunk(prompt: int, candidates: int) -> dict:
    return {
        "candidates": [],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": candidates},
    }


def adapter_for(turns: list[bytes], *, record: list[dict] | None = None) -> GeminiAdapter:
    """An adapter whose transport replays one prepared SSE body per turn."""
    remaining = list(turns)

    def handle(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(json.loads(request.content))
        body = remaining.pop(0) if remaining else sse(text_chunk("done"))
        return httpx.Response(200, content=body)

    return GeminiAdapter(
        model="gemini-2.5-flash", api_key="key-test", transport=httpx.MockTransport(handle)
    )


def spy_tool(name: str = "search_vault", reply: str = "found it") -> tuple[ToolSpec, list[dict]]:
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


async def collect(
    adapter: GeminiAdapter, tools: Sequence[ToolSpec], max_turns: int = 8, text: str = "hello"
) -> list:
    return [
        event
        async for event in adapter.run(
            system_prompt="be helpful",
            messages=[Message("user", text)],
            tools=tools,
            max_turns=max_turns,
        )
    ]


# --- request shape ----------------------------------------------------------


def test_a_tool_with_no_arguments_ships_without_a_parameters_key() -> None:
    """The single most likely cause of "Gemini will not call tools".

    `json_schema({})` produces `{"properties": {}}`, which Gemini rejects with a
    400 — and the rejection takes down the whole request, so every other tool in
    the belt stops working too, not just the empty one.
    """

    async def handler(_args: dict) -> dict:
        return text_result("ok")

    no_args = ToolSpec(
        name="list_tasks", description="all tasks", parameters=json_schema({}), handler=handler
    )
    with_args = ToolSpec(
        name="read_note",
        description="one note",
        parameters=json_schema({"path": {"type": "string"}}),
        handler=handler,
    )

    declarations = to_gemini_tools([no_args, with_args])[0]["functionDeclarations"]

    assert "parameters" not in declarations[0]
    assert declarations[1]["parameters"]["required"] == ["path"]


def test_schema_keys_gemini_does_not_know_are_stripped() -> None:
    """Gemini's dialect is an OpenAPI subset that 400s on an unknown key rather
    than ignoring it, so filtering has to happen before the request, not after
    the first failure in production."""

    async def handler(_args: dict) -> dict:
        return text_result("ok")

    spec = ToolSpec(
        name="odd",
        description="has extras",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tags": {"type": "array", "items": {"type": "string", "examples": ["a"]}}
            },
            "required": ["tags"],
        },
        handler=handler,
    )

    parameters = to_gemini_tools([spec])[0]["functionDeclarations"][0]["parameters"]

    assert "additionalProperties" not in parameters
    assert "examples" not in parameters["properties"]["tags"]["items"]
    assert parameters["properties"]["tags"]["items"]["type"] == "string"


@pytest.mark.anyio
async def test_the_system_prompt_is_its_own_field_not_a_turn() -> None:
    record: list[dict] = []
    adapter = adapter_for([sse(text_chunk("hi"))], record=record)

    await collect(adapter, [])

    assert record[0]["systemInstruction"] == {"parts": [{"text": "be helpful"}]}
    assert record[0]["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]


@pytest.mark.anyio
async def test_an_assistant_turn_is_sent_as_the_model_role() -> None:
    """Gemini's role is `model`; sending `assistant` is a 400."""
    record: list[dict] = []
    adapter = adapter_for([sse(text_chunk("hi"))], record=record)

    events = [
        event
        async for event in adapter.run(
            system_prompt="",
            messages=[
                Message("user", "first"),
                Message("assistant", "answered"),
                Message("user", "second"),
            ],
            tools=[],
            max_turns=4,
        )
    ]

    assert [c["role"] for c in record[0]["contents"]] == ["user", "model", "user"]
    assert any(isinstance(e, UsageReported) for e in events)


@pytest.mark.anyio
async def test_the_key_rides_in_the_google_header_and_the_url_names_the_model() -> None:
    seen: list[tuple[str | None, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("x-goog-api-key"), str(request.url)))
        return httpx.Response(200, content=sse(text_chunk("ok")))

    adapter = GeminiAdapter(
        model="gemini-2.5-pro", api_key="secret-key", transport=httpx.MockTransport(handle)
    )

    await collect(adapter, [])

    key, url = seen[0]
    assert key == "secret-key"
    assert url.endswith("/models/gemini-2.5-pro:streamGenerateContent?alt=sse")


# --- the tool loop ----------------------------------------------------------


@pytest.mark.anyio
async def test_a_function_call_round_trips_as_a_function_response() -> None:
    """Gemini has no tool role: results go back as a user turn of
    functionResponse parts, and the response body must be an object."""
    spec, seen = spy_tool()
    record: list[dict] = []
    adapter = adapter_for(
        [sse(call_chunk("search_vault", {"query": "graphs"})), sse(text_chunk("here you go"))],
        record=record,
    )

    events = await collect(adapter, [spec])

    assert seen == [{"query": "graphs"}]
    started = next(e for e in events if isinstance(e, ToolStarted))
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert started.call_id == finished.call_id, "the trace pairs start and end by call id"

    second = record[1]["contents"]
    assert second[1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "search_vault", "args": {"query": "graphs"}}}],
    }
    assert second[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "search_vault", "response": {"result": "found it"}}}
        ],
    }


@pytest.mark.anyio
async def test_two_calls_in_one_turn_stay_two_calls() -> None:
    """The reason this adapter exists rather than routing through the OpenAI
    shim, whose fragments do not carry the index the buffer joins on."""
    spec_a, seen_a = spy_tool("search_vault")
    spec_b, seen_b = spy_tool("read_note", reply="note text")
    adapter = adapter_for(
        [
            sse(
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"functionCall": {"name": "search_vault", "args": {"q": 1}}},
                                    {"functionCall": {"name": "read_note", "args": {"p": "a.md"}}},
                                ],
                            }
                        }
                    ]
                }
            ),
            sse(text_chunk("both done")),
        ]
    )

    events = await collect(adapter, [spec_a, spec_b])

    assert seen_a == [{"q": 1}]
    assert seen_b == [{"p": "a.md"}]
    assert len({e.call_id for e in events if isinstance(e, ToolStarted)}) == 2


@pytest.mark.anyio
async def test_the_last_turn_forbids_tools_and_says_it_hit_the_limit() -> None:
    spec, _seen = spy_tool()
    record: list[dict] = []
    adapter = adapter_for(
        [sse(call_chunk("search_vault", {"query": "a"})), sse(text_chunk("partial"))],
        record=record,
    )

    events = await collect(adapter, [spec], max_turns=2)

    modes = [p["toolConfig"]["functionCallingConfig"]["mode"] for p in record]
    assert modes == ["AUTO", "NONE"]
    assert next(e for e in events if isinstance(e, Notice)).kind == "turn_limit"


@pytest.mark.anyio
async def test_a_handler_failure_becomes_text_the_model_can_recover_from() -> None:
    async def boom(_args: dict) -> dict:
        raise RuntimeError("no note at ghost.md")

    spec = ToolSpec(
        name="read_note",
        description="one note",
        parameters=json_schema({"path": {"type": "string"}}),
        handler=boom,
    )
    record: list[dict] = []
    adapter = adapter_for(
        [sse(call_chunk("read_note", {"path": "ghost.md"})), sse(text_chunk("sorry"))],
        record=record,
    )

    await collect(adapter, [spec])

    response = record[1]["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert response["result"] == "error: no note at ghost.md"


# --- streaming and usage ----------------------------------------------------


@pytest.mark.anyio
async def test_text_streams_as_deltas() -> None:
    adapter = adapter_for([sse(text_chunk("Dijkstra "), text_chunk("finds paths"))])

    events = await collect(adapter, [])

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Dijkstra ", "finds paths"]


@pytest.mark.anyio
async def test_usage_is_assigned_within_a_turn_and_summed_across_them() -> None:
    """usageMetadata is cumulative per response — adding it inside one turn
    would multiply-count, the same trap the Anthropic adapter documents."""
    spec, _seen = spy_tool()
    adapter = adapter_for(
        [
            sse(call_chunk("search_vault", {"query": "a"}), usage_chunk(10, 4), usage_chunk(10, 9)),
            sse(text_chunk("done"), usage_chunk(30, 6)),
        ]
    )

    events = await collect(adapter, [spec])

    usage = next(e for e in events if isinstance(e, UsageReported))
    assert (usage.usage["input_tokens"], usage.usage["output_tokens"]) == (40, 15)


@pytest.mark.anyio
async def test_abandoning_the_stream_still_reports_what_was_counted() -> None:
    """The turn the user walked away from is the one that must not be lost.

    Closing the outer generator does not run the inner one's `finally` until
    the GC gets to it, so `partial_usage` reads the in-flight turn directly
    rather than trusting that fold to have happened.
    """
    adapter = adapter_for([sse(text_chunk("long "), usage_chunk(70, 5), text_chunk("answer"))])

    stream = adapter.run(
        system_prompt="", messages=[Message("user", "hi")], tools=[], max_turns=4
    )
    assert isinstance(await stream.__anext__(), TextDelta)
    assert isinstance(await stream.__anext__(), TextDelta)  # past the usage chunk
    await stream.aclose()

    partial = adapter.partial_usage()
    assert partial is not None
    assert (partial.usage["input_tokens"], partial.usage["output_tokens"]) == (70, 5)


# --- errors and listing -----------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, {"error": {"message": "model not found"}}, "does the key have access"),
        (400, {"error": {"message": "invalid schema"}}, "check the Google AI Studio API key"),
        (429, {"error": {"message": "quota"}}, "quota for this model"),
    ],
)
async def test_http_errors_surface_actionable_messages(status, body, expected) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    adapter = GeminiAdapter(
        model="gemini-2.5-flash", api_key="k", transport=httpx.MockTransport(handle)
    )

    with pytest.raises(AgentError, match=expected):
        await collect(adapter, [])


@pytest.mark.anyio
async def test_list_models_drops_the_prefix_and_the_models_that_cannot_generate() -> None:
    """The same listing carries embedding and legacy models; offering one would
    hand the user a model that 404s the moment they pick it."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1beta/models")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-pro",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-2.5-flash",
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    assert await list_models("k", transport=httpx.MockTransport(handle)) == [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]
