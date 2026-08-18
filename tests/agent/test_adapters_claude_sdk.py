"""Tests for ClaudeSDKAdapter's tool-event handling.

``ClaudeSDKAdapter`` imports ``claude_agent_sdk`` *inside* its methods rather
than at module scope, specifically so a fake module can be substituted here
via ``monkeypatch.setitem(sys.modules, "claude_agent_sdk", ...)`` before the
adapter ever does its own import. If those imports are ever "tidied" up to
module scope, this whole file silently stops testing anything real — the
fake module would need to exist before ``backend.agent.adapters`` is first
imported anywhere in the process, which pytest cannot guarantee.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.agent.adapters import (
    ClaudeSDKAdapter,
    TextDelta,
    ToolFinished,
    ToolSpec,
    ToolStarted,
    ToolSummary,
    UsageReported,
    json_schema,
    text_result,
)

# --- fake claude_agent_sdk ---------------------------------------------------
#
# Plain dataclasses standing in for the real SDK's message/block types.
# Only the fields the adapter actually reads are modeled.


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool | None = None


@dataclass
class AssistantMessage:
    content: list[Any]


@dataclass
class UserMessage:
    content: Any


@dataclass
class ResultMessage:
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class StreamEvent:
    event: dict[str, Any]


class ClaudeAgentOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def create_sdk_mcp_server(name: str, tools: list[Any]) -> Any:
    return {"name": name, "tools": tools}


def tool(name: str, description: str, parameters: dict[str, Any]) -> Any:
    """The real `tool()` is a decorator factory; the fake just passes through."""

    def decorator(handler: Any) -> Any:
        return handler

    return decorator


def make_fake_sdk_module(script: list[Any]) -> types.ModuleType:
    """A ``claude_agent_sdk`` stand-in whose client replays ``script``."""

    class FakeClaudeSDKClient:
        def __init__(self, options: Any) -> None:
            self.options = options

        async def __aenter__(self) -> FakeClaudeSDKClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def query(self, message: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[Any]:
            for message in script:
                yield message

    module = types.ModuleType("claude_agent_sdk")
    module.AssistantMessage = AssistantMessage
    module.ClaudeAgentOptions = ClaudeAgentOptions
    module.ClaudeSDKClient = FakeClaudeSDKClient
    module.ResultMessage = ResultMessage
    module.StreamEvent = StreamEvent
    module.TextBlock = TextBlock
    module.ToolResultBlock = ToolResultBlock
    module.ToolUseBlock = ToolUseBlock
    module.UserMessage = UserMessage
    module.create_sdk_mcp_server = create_sdk_mcp_server
    module.tool = tool
    return module


def install_fake_sdk(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> None:
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", make_fake_sdk_module(script))


def spy_tool(name: str = "search_vault") -> tuple[ToolSpec, list[dict]]:
    seen: list[dict] = []

    async def handler(args: dict) -> dict:
        seen.append(args)
        return text_result("found it")

    return (
        ToolSpec(name, "search the vault", json_schema({"query": {"type": "string"}}), handler),
        seen,
    )


async def collect(adapter: ClaudeSDKAdapter, tools: list[ToolSpec]) -> list:
    return [
        event
        async for event in adapter.run(
            system_prompt="be helpful",
            user_message="what did I write about Dijkstra?",
            tools=tools,
            max_turns=4,
        )
    ]


def texts(events: list) -> str:
    return "".join(event.text for event in events if isinstance(event, TextDelta))


# --- tests --------------------------------------------------------------


@pytest.mark.anyio
async def test_tool_use_and_result_become_started_and_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="t1", name="mcp__argus__search_vault", input={"query": "x"})
            ]
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="found it")]),
        AssistantMessage(content=[TextBlock(text="It's in algorithms.md")]),
        ResultMessage(usage={"input_tokens": 10, "output_tokens": 5}),
    ]
    install_fake_sdk(monkeypatch, script)
    spec, _ = spy_tool()
    adapter = ClaudeSDKAdapter(model="claude-sonnet-5")

    events = await collect(adapter, [spec])

    started = next(e for e in events if isinstance(e, ToolStarted))
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert started == ToolStarted(call_id="t1", name="search_vault", args={"query": "x"})
    assert finished.call_id == "t1"
    assert finished.name == "search_vault"
    assert finished.summary == ToolSummary(label="search_vault", ok=True)
    assert texts(events) == "It's in algorithms.md"
    assert sum(isinstance(e, UsageReported) for e in events) == 1


@pytest.mark.anyio
async def test_namespaced_tool_name_is_stripped_before_summarize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`by_name.get(local_name)` only finds the ToolSpec if the mcp__ prefix is gone."""

    def summarize(args: dict, result_text: str) -> ToolSummary:
        return ToolSummary(label=f"searched for {args['query']}", detail=result_text)

    async def handler(_args: dict) -> dict:
        return text_result("found it")

    spec = ToolSpec(
        name="search_vault",
        description="search the vault",
        parameters=json_schema({"query": {"type": "string"}}),
        handler=handler,
        summarize=summarize,
    )
    script = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="t1", name="mcp__argus__search_vault", input={"query": "x"})
            ]
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="found it")]),
        ResultMessage(usage={}),
    ]
    install_fake_sdk(monkeypatch, script)
    adapter = ClaudeSDKAdapter(model="claude-sonnet-5", tool_namespace="argus")

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == ToolSummary(label="searched for x", detail="found it")


@pytest.mark.anyio
async def test_list_tool_result_content_is_flattened_like_an_mcp_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_result_text: list[str] = []

    def summarize(_args: dict, result_text: str) -> ToolSummary:
        seen_result_text.append(result_text)
        return ToolSummary(label="ok")

    async def handler(_args: dict) -> dict:
        return text_result("unused")

    spec = ToolSpec(
        name="search_vault",
        description="search the vault",
        parameters=json_schema({}),
        handler=handler,
        summarize=summarize,
    )
    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="mcp__argus__search_vault", input={})]
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[
                        {"type": "text", "text": "line1"},
                        {"type": "text", "text": "line2"},
                    ],
                )
            ]
        ),
        ResultMessage(usage={}),
    ]
    install_fake_sdk(monkeypatch, script)
    adapter = ClaudeSDKAdapter(model="claude-sonnet-5")

    await collect(adapter, [spec])

    assert seen_result_text == ["line1\nline2"]


@pytest.mark.anyio
async def test_is_error_yields_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="mcp__argus__search_vault", input={})]
        ),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)]
        ),
        ResultMessage(usage={}),
    ]
    install_fake_sdk(monkeypatch, script)
    spec, _ = spy_tool()
    adapter = ClaudeSDKAdapter(model="claude-sonnet-5")

    events = await collect(adapter, [spec])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary.ok is False


@pytest.mark.anyio
async def test_tool_use_in_stream_event_does_not_double_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial-message streaming carries tool_use too; only the assembled
    AssistantMessage may be read for it, or this would double-emit ToolStarted."""
    script = [
        StreamEvent(
            event={
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "mcp__argus__search_vault",
                    "input": {},
                },
            }
        ),
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="mcp__argus__search_vault", input={})]
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="found it")]),
        ResultMessage(usage={}),
    ]
    install_fake_sdk(monkeypatch, script)
    spec, _ = spy_tool()
    adapter = ClaudeSDKAdapter(model="claude-sonnet-5")

    events = await collect(adapter, [spec])

    assert sum(isinstance(e, ToolStarted) for e in events) == 1
