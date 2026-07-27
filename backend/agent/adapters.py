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
  :func:`backend.telemetry.usage.record_result_usage` already duck-types on.
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

    :func:`backend.telemetry.usage.record_result_usage` duck-types on a ``.usage``
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


# --- resolution -------------------------------------------------------------


def find_entry(models: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The registry entry called ``name``, or None."""
    return next((entry for entry in models if entry.get("name") == name), None)


def adapter_for_entry(
    entry: dict[str, Any],
    *,
    tool_namespace: str = "argus",
    disallowed_tools: Sequence[str] = ("Bash", "Write", "Edit"),
    api_key: str | None = None,
) -> AgentAdapter:
    """Build the adapter one registry entry describes.

    ``api_key`` overrides the keyring lookup. That is what lets the registration
    flow test a key the user has just typed but not yet saved — the key stays in
    memory for that one call and is stored only once the model is known to work.
    """
    from backend.agent.credentials import get_key

    name = str(entry["name"])
    provider = str(entry.get("provider") or PROVIDER_CLAUDE_CLI)
    # Hosted entries often label a model differently from the id the provider
    # expects ("groq-llama" serving "llama-3.3-70b-versatile").
    model_id = str(entry.get("model_id") or name)
    resolved_key = api_key or get_key(entry.get("key_ref"))

    if provider == PROVIDER_CLAUDE_CLI:
        return ClaudeSDKAdapter(
            model=name,
            tool_namespace=tool_namespace,
            disallowed_tools=tuple(disallowed_tools),
        )

    if provider == PROVIDER_ANTHROPIC_API:
        from backend.agent.anthropic_api import DEFAULT_ENDPOINT, AnthropicAPIAdapter

        if not resolved_key:
            raise AgentError(
                f"no API key stored for {name!r} — re-add it under /system to store one"
            )
        return AnthropicAPIAdapter(
            model=model_id,
            api_key=resolved_key,
            endpoint=str(entry.get("endpoint") or DEFAULT_ENDPOINT),
        )

    if provider == PROVIDER_OPENAI_COMPAT:
        from backend.agent.openai_compat import OpenAICompatAdapter

        endpoint = entry.get("endpoint")
        if not endpoint:
            raise AgentError(f"model {name!r} has no endpoint — re-add it under /system")
        return OpenAICompatAdapter(model=model_id, endpoint=str(endpoint), api_key=resolved_key)

    raise AgentError(
        f"model {name!r} uses unknown provider {provider!r} "
        f"(expected one of {', '.join(KNOWN_PROVIDERS)})"
    )


def resolve_adapter(
    settings: Any,
    model: str | None = None,
    *,
    tool_namespace: str = "argus",
    disallowed_tools: Sequence[str] = ("Bash", "Write", "Edit"),
    fallback_model: str | None = None,
) -> AgentAdapter:
    """The adapter for a registry model name.

    ``model=None`` keeps each call site's historical behavior: it runs
    ``fallback_model`` (the module's long-standing ``MODEL`` constant) on the
    Claude Code path, so nothing changes for callers that never opt in. Passing
    a name routes through the registry, whatever provider backs it.
    """
    if not model:
        if fallback_model:
            return ClaudeSDKAdapter(
                model=fallback_model,
                tool_namespace=tool_namespace,
                disallowed_tools=tuple(disallowed_tools),
            )
        model = settings.default_model

    entry = find_entry(settings.models, model)
    if entry is None:
        raise RuntimeError(f"unknown model {model!r} — register it under /system first")
    return adapter_for_entry(
        entry, tool_namespace=tool_namespace, disallowed_tools=disallowed_tools
    )


# --- capability probe -------------------------------------------------------

PROBE_TOOL_NAME = "argus_probe"
PROBE_PROMPT = (
    "Verify your tool access. Call the argus_probe tool exactly once with "
    'answer set to "ready". Do not reply with prose.'
)
PROBE_SYSTEM = "You are verifying tool access. Use the provided tool; do not answer in text."


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a live tool-calling check against one model."""

    ok: bool
    detail: str
    tool_calling: bool = False
    latency_ms: int = 0


async def probe_tool_calling(adapter: AgentAdapter) -> ProbeResult:
    """Ask a model to make one real tool call.

    Argus has no no-tools fallback on any provider, because chat's citation
    invariant (I6) and the planner's suggest-then-approve invariant (I1) are
    both enforced *through* tools — a model that cannot call them would look
    like it was working while quietly violating both. So registration gates on
    this: one throwaway call, and a model that will not use the tool is
    rejected with a reason the user can read.
    """
    import time

    called: list[str] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        called.append(str(args.get("answer", "")))
        return text_result("ok")

    spec = ToolSpec(
        name=PROBE_TOOL_NAME,
        description="Confirm tool access. Call this with answer='ready'.",
        parameters=json_schema({"answer": {"type": "string"}}),
        handler=handler,
    )

    started = time.monotonic()
    try:
        async for _event in adapter.run(
            system_prompt=PROBE_SYSTEM, user_message=PROBE_PROMPT, tools=[spec], max_turns=2
        ):
            pass
    except Exception as exc:  # noqa: BLE001 - every failure becomes a readable verdict
        return ProbeResult(
            ok=False, detail=str(exc), latency_ms=int((time.monotonic() - started) * 1000)
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    if not called:
        return ProbeResult(
            ok=False,
            detail=(
                "the model answered but never called the test tool — Argus needs "
                "tool calling for vault citations and planner proposals"
            ),
            latency_ms=latency_ms,
        )
    return ProbeResult(
        ok=True,
        detail="reachable, and tool calling works",
        tool_calling=True,
        latency_ms=latency_ms,
    )
