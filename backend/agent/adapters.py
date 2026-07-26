"""Provider-agnostic agent runtime.

Argus's three agent call sites — chat (:mod:`backend.agent.runtime`), the day
planner (:mod:`backend.agent.planner`) and one-shot generation
(:mod:`backend.agent.generate`) — used to build ``claude_agent_sdk``'s
``@tool``-decorated objects directly, which are SDK-specific values rather than
portable data. This module turns a tool into plain data (:class:`ToolSpec`) and
puts one small interface (:class:`AgentAdapter`) in front of every backend, so
the same tool belt runs on Claude Code, on the Anthropic API, or on any
OpenAI-compatible endpoint (Ollama, Groq, Together, Fireworks, OpenRouter,
Codex, Gemini).

Three rules hold on every adapter:

* **Tool calling is mandatory.** Chat's citation invariant (I6) and the
  planner's suggest-then-approve invariant (I1) are both enforced *through
  tools*. A model that cannot call tools would silently degrade both, so
  there is no no-tools fallback — :func:`probe_tool_calling` gates registration.
* **Handlers are shared.** A :class:`ToolSpec` handler is the same coroutine
  whichever adapter runs it, so vault reads behave identically across
  providers (and can be re-exposed over MCP — see
  :mod:`backend.agent.mcp_server`).
* **Usage logging is uniform.** Adapters emit :class:`UsageReported`, whose
  ``.usage`` mapping is exactly the shape
  :func:`backend.usage.record_result_usage` already duck-types on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Providers understood by the registry. "anthropic" is the historical value and
# still means "the Claude Code CLI via claude-agent-sdk" (subscription auth,
# invariant I5) — it is deliberately unchanged so existing .argus/models.json
# files keep working untouched.
PROVIDER_CLAUDE_CLI = "anthropic"
PROVIDER_ANTHROPIC_API = "anthropic-api"
PROVIDER_OPENAI_COMPAT = "openai-compat"
KNOWN_PROVIDERS = (PROVIDER_CLAUDE_CLI, PROVIDER_ANTHROPIC_API, PROVIDER_OPENAI_COMPAT)

# Providers whose traffic leaves the machine. Local endpoints (Ollama and
# friends) are the whole point of the "notes never leave your machine" promise,
# so the UI badges this distinction — see `is_local_endpoint`.
HOSTED_PROVIDERS = (PROVIDER_CLAUDE_CLI, PROVIDER_ANTHROPIC_API)

LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")

DEFAULT_TIMEOUT_SECONDS = 120.0


class AgentError(RuntimeError):
    """A provider call failed in a way the user needs to see."""


# --- tools ------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """One tool, as plain data any backend can translate.

    ``parameters`` is a JSON Schema object (``{"type": "object", "properties":
    {...}, "required": [...]}``) — the format the Anthropic Messages API and
    the OpenAI function-calling API both take directly, and one of the forms
    ``claude_agent_sdk.tool`` accepts.

    ``handler`` returns an MCP-shaped result (``{"content": [{"type": "text",
    "text": ...}]}``); :func:`flatten_tool_result` renders that down to the
    plain string the non-MCP APIs want.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]


def text_result(payload: Any) -> dict[str, Any]:
    """Wrap a payload as an MCP text content result (the handler return shape)."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def flatten_tool_result(result: Any) -> str:
    """Render a handler's MCP-shaped result as the plain text non-MCP APIs want."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        blocks = result.get("content")
        if isinstance(blocks, list):
            return "\n".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type", "text") == "text"
            )
    return json.dumps(result, ensure_ascii=False)


def json_schema(
    properties: dict[str, Any], required: Sequence[str] | None = None
) -> dict[str, Any]:
    """Build a JSON Schema object, defaulting every property to required.

    Tool arguments are almost always all-required in Argus's tool belt; the few
    optional ones (``search_vault``'s ``course`` filter) pass ``required``
    explicitly.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties if required is None else required),
    }


# --- events -----------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant text. Chat streams these straight to the browser."""

    text: str


@dataclass(frozen=True)
class ToolUsed:
    """A tool the model actually invoked. Informational — handlers already ran."""

    name: str


@dataclass(frozen=True)
class UsageReported:
    """Token accounting for one run.

    :func:`backend.usage.record_result_usage` duck-types on a ``.usage``
    attribute that is either a dict or has a ``__dict__``, so this class drops
    straight into every existing call site with no changes there.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def usage(self) -> dict[str, int]:
        """The mapping ``record_result_usage`` reads."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


AgentEvent = TextDelta | ToolUsed | UsageReported


# --- adapter interface ------------------------------------------------------


class AgentAdapter(Protocol):
    """One agent turn against one provider.

    Implementations run the whole completion→tool-call→result loop and yield
    events as they go. The Claude SDK runs that loop internally; the HTTP
    adapters run it by hand, because plain chat-completion endpoints have no
    equivalent orchestrator.
    """

    model: str
    provider: str

    def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        """Yield text deltas, tool-use notices, and a final usage report."""
        ...


# --- Claude Code (claude-agent-sdk) -----------------------------------------


@dataclass
class ClaudeSDKAdapter:
    """Today's path, unchanged: the Claude Code CLI via ``claude-agent-sdk``.

    Auth is the user's Claude subscription login (invariant I5) — no API key is
    ever set here; that is what :class:`AnthropicAPIAdapter` is for. Requires
    Claude Code installed and signed in, which is exactly why it is now one
    provider among several rather than the only one.

    ``tool_namespace`` matches the SDK MCP server name the call site used
    historically (``argus`` for chat, ``planner`` for the planner) so the
    ``mcp__<namespace>__<tool>`` allow-list keeps its established shape.
    """

    model: str
    tool_namespace: str = "argus"
    disallowed_tools: tuple[str, ...] = ("Bash", "Write", "Edit")
    provider: str = field(default=PROVIDER_CLAUDE_CLI, init=False)

    def _to_sdk_tools(self, tools: Sequence[ToolSpec]) -> list[Any]:
        """Translate ToolSpecs into the SDK's decorated tool objects."""
        from claude_agent_sdk import tool as sdk_tool

        built = []
        for spec in tools:
            # `tool()` accepts a JSON Schema dict directly, so ToolSpec.parameters
            # passes through without a second schema dialect in the codebase.
            built.append(sdk_tool(spec.name, spec.description, spec.parameters)(spec.handler))
        return built

    async def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        if not tools:
            async for event in self._run_toolless(system_prompt, user_message, max_turns):
                yield event
            return
        async for event in self._run_with_tools(system_prompt, user_message, tools, max_turns):
            yield event

    async def _run_toolless(
        self, system_prompt: str, user_message: str, max_turns: int
    ) -> AsyncIterator[AgentEvent]:
        """The one-shot `query()` path generate.py has always used."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=self.model,
            max_turns=max_turns,
            disallowed_tools=list(self.disallowed_tools),
            **({"system_prompt": system_prompt} if system_prompt else {}),
        )
        async for message in query(prompt=user_message, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        yield TextDelta(block.text)
            elif isinstance(message, ResultMessage):
                yield _usage_from_sdk(message)

    async def _run_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            StreamEvent,
            TextBlock,
            create_sdk_mcp_server,
        )

        server = create_sdk_mcp_server(self.tool_namespace, tools=self._to_sdk_tools(tools))
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=system_prompt,
            mcp_servers={self.tool_namespace: server},
            allowed_tools=[f"mcp__{self.tool_namespace}__{spec.name}" for spec in tools],
            disallowed_tools=list(self.disallowed_tools),
            include_partial_messages=True,
            max_turns=max_turns,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            streamed_any = False
            async for event in client.receive_response():
                if isinstance(event, StreamEvent):
                    raw = event.event
                    if raw.get("type") == "content_block_delta":
                        delta = raw.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            streamed_any = True
                            yield TextDelta(delta["text"])
                elif isinstance(event, AssistantMessage) and not streamed_any:
                    # Partial-message streaming is best-effort; fall back to the
                    # assembled blocks so a run never yields nothing.
                    for block in event.content:
                        if isinstance(block, TextBlock) and block.text:
                            yield TextDelta(block.text)
                elif isinstance(event, ResultMessage):
                    yield _usage_from_sdk(event)


def _usage_from_sdk(message: Any) -> UsageReported:
    """Read an SDK ResultMessage's usage mapping into a UsageReported."""
    usage = getattr(message, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = vars(usage)
    return UsageReported(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
    )


# --- helpers ----------------------------------------------------------------


def is_local_endpoint(endpoint: str | None) -> bool:
    """True when an endpoint URL points at this machine.

    Drives the LOCAL/HOSTED badge: with a local endpoint the user's notes never
    leave the machine, which is the privacy promise the README makes. Anything
    else is a network hop and is badged as such.
    """
    if not endpoint:
        return False
    from urllib.parse import urlparse

    host = (urlparse(endpoint).hostname or "").lower()
    return host in LOCAL_HOSTS


def entry_is_local(entry: dict[str, Any]) -> bool:
    """True when a registry entry runs entirely on this machine."""
    if entry.get("provider") in HOSTED_PROVIDERS:
        return False
    return is_local_endpoint(entry.get("endpoint"))
