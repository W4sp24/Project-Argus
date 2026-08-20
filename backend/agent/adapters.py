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
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

# Providers understood by the registry. "anthropic" is the historical value and
# still means "the Claude Code CLI via claude-agent-sdk" (subscription auth,
# invariant I5) — it is deliberately unchanged so existing .argus/models.json
# files keep working untouched.
PROVIDER_CLAUDE_CLI = "anthropic"
PROVIDER_ANTHROPIC_API = "anthropic-api"
PROVIDER_OPENAI_COMPAT = "openai-compat"
# Gemini gets its own provider rather than riding the OpenAI-compatible shim
# Google also publishes: that shim's streamed tool-call fragments do not
# reliably carry an `index`, which is the only key `_ToolCallBuffer` can join
# on, so two calls in one turn would silently become one. See
# backend/agent/gemini_api.py.
PROVIDER_GEMINI = "gemini"
KNOWN_PROVIDERS = (
    PROVIDER_CLAUDE_CLI,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_GEMINI,
)

# Providers whose traffic leaves the machine. Local endpoints (Ollama and
# friends) are the whole point of the "notes never leave your machine" promise,
# so the UI badges this distinction — see `is_local_endpoint`.
HOSTED_PROVIDERS = (PROVIDER_CLAUDE_CLI, PROVIDER_ANTHROPIC_API, PROVIDER_GEMINI)

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

    ``summarize`` is optional and defaulted so every existing construction
    site keeps working untouched. Its signature is ``(args, result_text) ->
    ToolSummary``, where ``result_text`` is :func:`flatten_tool_result`'s
    output rather than the handler's raw MCP-shaped return — that is what lets
    one summarizer work unchanged across the SDK adapter and both HTTP
    adapters, which otherwise disagree about tool-result shape.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    summarize: Callable[[dict[str, Any], str], ToolSummary] | None = None


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


# --- conversation -------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """One turn of conversation, provider-agnostic.

    This is the wire shape every adapter now takes instead of a bare string —
    see :func:`require_user_turn` for the contract on the sequence as a whole.
    """

    role: Literal["user", "assistant"]
    text: str


def require_user_turn(messages: Sequence[Message]) -> Sequence[Message]:
    """Validate that ``messages`` ends on a user turn the model can answer.

    A run whose last message is not the user's has nothing to respond to —
    catching that here, with a message that says what was wrong, is far more
    legible than letting it reach a provider as a mid-stream 400. Every
    adapter calls this first, before it spends any tokens building a request.
    """
    if not messages:
        raise AgentError("no messages to run — at least one user message is required")
    if messages[-1].role != "user":
        raise AgentError(
            f"the last message must be from the user, not {messages[-1].role!r} — "
            "a run can only answer a question that was actually asked"
        )
    return messages


# --- events -----------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant text. Chat streams these straight to the browser."""

    text: str


@dataclass(frozen=True)
class ToolSummary:
    """What a tool did, in the shape a UI chip needs.

    ``ok`` lives here rather than on :class:`ToolFinished` so there is exactly
    one source of truth for "did this work" — a caller checking the event
    would otherwise have to reconcile two possibly-disagreeing booleans.
    """

    label: str
    detail: str = ""
    paths: tuple[str, ...] = ()
    ok: bool = True


@dataclass(frozen=True)
class ToolStarted:
    """A tool call the model just made, before its handler has run.

    ``call_id`` is the provider's id for this call (or, for OpenAI-compatible
    providers that omit one, the synthesized ``call_{index}``) — it is what
    lets a UI pair this event with the matching :class:`ToolFinished`.
    """

    call_id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolFinished:
    """The handler for a started tool call has returned (or failed).

    ``summary`` is never None — adapters that have no :attr:`ToolSpec.summarize`
    (or whose summarizer blew up) fall back to ``ToolSummary(label=name)`` via
    :func:`summarize_tool_result`, so a UI never has to special-case a missing
    summary.
    """

    call_id: str
    name: str
    summary: ToolSummary


@dataclass(frozen=True)
class Notice:
    """Something the user should know about the run itself, not its answer.

    Today there is exactly one: the agent used every tool turn it was allowed.
    That case was completely silent before — the loop simply fell out, the last
    tool result was never read by anything, and the reply stopped mid-thought
    with no explanation anywhere. A model that searches badly and keeps
    retrying looks, from the outside, exactly like a model that cannot search
    at all.
    """

    kind: Literal["turn_limit"]
    detail: str


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


AgentEvent = TextDelta | ToolStarted | ToolFinished | Notice | UsageReported


def summarize_tool_result(
    spec: ToolSpec | None, name: str, args: dict[str, Any], result_text: str
) -> ToolSummary:
    """The one choke point every adapter calls to build a :class:`ToolFinished`.

    A broken summarizer must never break a chat turn — it runs after the tool
    already did its work and the model is waiting on the result — so any
    exception here is swallowed, not logged-and-reraised, and degrades to the
    same fallback as having no summarizer at all: ``ToolSummary(label=name)``.

    That fallback also carries ``ok``. The HTTP adapters' ``_dispatch`` never
    raises — it turns a handler exception or an unknown tool into an
    ``"error: ..."`` string so the model can recover — so without a summarizer
    to say otherwise, a result text starting with ``"error:"`` is the only
    signal available that the call failed.
    """
    if spec is not None and spec.summarize is not None:
        try:
            return spec.summarize(args, result_text)
        except Exception:  # noqa: BLE001 - degrade to the fallback, never break the turn
            pass
    return ToolSummary(label=name, ok=not result_text.startswith("error:"))


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
        messages: Sequence[Message],
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

    def _local_name(self, raw: str) -> str:
        """Strip the ``mcp__{namespace}__`` prefix the SDK puts on tool names.

        Without this, ``ToolStarted``/``ToolFinished`` would carry a name that
        matches nothing in ``by_name`` on the frontend and nothing in the tool
        belt's own ``ToolSpec.name`` — the whole point of routing tool events
        through the same ``AgentEvent`` union as the HTTP adapters is that a
        consumer never has to know which adapter produced one.
        """
        prefix = f"mcp__{self.tool_namespace}__"
        return raw[len(prefix) :] if raw.startswith(prefix) else raw

    async def run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        require_user_turn(messages)
        # The SDK takes one prompt string, not a message list, so history is
        # serialized once here and both paths below share a plain prompt —
        # neither needs to know Message exists. serialize_history's one-message
        # branch is what keeps this byte-identical to the pre-history prompt.
        from backend.agent.history import serialize_history

        prompt = serialize_history(messages)
        if not tools:
            async for event in self._run_toolless(system_prompt, prompt, max_turns):
                yield event
            return
        async for event in self._run_with_tools(system_prompt, prompt, tools, max_turns):
            yield event

    async def _run_toolless(
        self, system_prompt: str, prompt: str, max_turns: int
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
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        yield TextDelta(block.text)
            elif isinstance(message, ResultMessage):
                yield _usage_from_sdk(message)

    async def _run_with_tools(
        self,
        system_prompt: str,
        prompt: str,
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
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
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
        by_name = {spec.name: spec for spec in tools}
        # ToolResultBlock only carries tool_use_id, so the name and args a
        # ToolFinished needs are stashed here when its ToolStarted goes out.
        pending: dict[str, tuple[str, dict[str, Any]]] = {}

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            streamed_any = False
            async for event in client.receive_response():
                if isinstance(event, StreamEvent):
                    # Tool use also appears here under include_partial_messages,
                    # but only the assembled AssistantMessage is read for it
                    # below — reading both would double-emit ToolStarted.
                    raw = event.event
                    if raw.get("type") == "content_block_delta":
                        delta = raw.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            streamed_any = True
                            yield TextDelta(delta["text"])
                elif isinstance(event, AssistantMessage):
                    for block in event.content:
                        if isinstance(block, TextBlock) and block.text and not streamed_any:
                            # Partial-message streaming is best-effort; fall back
                            # to the assembled blocks so a run never yields nothing.
                            yield TextDelta(block.text)
                        elif isinstance(block, ToolUseBlock):
                            local_name = self._local_name(block.name)
                            args = block.input or {}
                            pending[block.id] = (local_name, args)
                            yield ToolStarted(call_id=block.id, name=local_name, args=args)
                elif isinstance(event, UserMessage):
                    content = event.content
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, ToolResultBlock):
                                local_name, args = pending.pop(
                                    block.tool_use_id, (block.tool_use_id, {})
                                )
                                result_text = _flatten_sdk_tool_result(block.content)
                                summary = summarize_tool_result(
                                    by_name.get(local_name), local_name, args, result_text
                                )
                                if block.is_error:
                                    summary = replace(summary, ok=False)
                                yield ToolFinished(
                                    call_id=block.tool_use_id, name=local_name, summary=summary
                                )
                elif isinstance(event, ResultMessage):
                    yield _usage_from_sdk(event)


def _flatten_sdk_tool_result(content: str | list[dict[str, Any]] | None) -> str:
    """Normalize a ``ToolResultBlock.content`` into what `flatten_tool_result` wants.

    The SDK's result shape is *not* the ``{"content": [...]}`` MCP envelope
    handlers return on the HTTP adapters — it is the block's content directly,
    already unwrapped by the SDK. A string passes straight through; a list of
    content-part dicts is re-wrapped in that envelope so the same
    `flatten_tool_result` still applies; ``None`` (a tool that returned
    nothing) becomes ``""``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return flatten_tool_result({"content": content})


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
    from backend.agent.credentials import KeyringUnavailableError, get_key

    name = str(entry["name"])
    provider = str(entry.get("provider") or PROVIDER_CLAUDE_CLI)
    # Hosted entries often label a model differently from the id the provider
    # expects ("groq-llama" serving "llama-3.3-70b-versatile").
    model_id = str(entry.get("model_id") or name)
    try:
        resolved_key = api_key or get_key(entry.get("key_ref"))
    except KeyringUnavailableError as exc:
        # Not the same as "you never saved a key" — telling the user to re-add
        # one they already have is how this used to waste their time.
        raise AgentError(f"cannot use {name!r}: {exc}") from exc

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

    if provider == PROVIDER_GEMINI:
        from backend.agent.gemini_api import DEFAULT_ENDPOINT as GEMINI_ENDPOINT
        from backend.agent.gemini_api import GeminiAdapter

        if not resolved_key:
            raise AgentError(
                f"no API key stored for {name!r} — re-add it under /system to store one"
            )
        return GeminiAdapter(
            model=model_id,
            api_key=resolved_key,
            endpoint=str(entry.get("endpoint") or GEMINI_ENDPOINT),
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


def _preferred_model(settings: Any) -> str | None:
    """The user's chosen default, when there is a settings object to ask.

    Tolerant on purpose: ``settings`` is typed ``Any`` because call sites pass
    fakes, and a caller with no settings (or a half-built one) should fall back
    rather than crash.
    """
    if settings is None:
        return None
    try:
        chosen = settings.default_model
    except Exception:  # noqa: BLE001 - a fake or partial settings object
        return None
    return str(chosen) if chosen else None


def resolve_adapter(
    settings: Any,
    model: str | None = None,
    *,
    tool_namespace: str = "argus",
    disallowed_tools: Sequence[str] = ("Bash", "Write", "Edit"),
    fallback_model: str | None = None,
) -> AgentAdapter:
    """The adapter for a registry model name.

    ``model=None`` means "whatever the user chose under /system" — it resolves
    through ``settings.default_model`` like a named model would. That is what
    makes Argus's own unattended calls (the 07:00 briefing, coursework
    generation, the planner) run on a local Ollama model on a machine that has
    never had Claude Code installed.

    ``fallback_model`` is the last resort, for callers with no ``settings`` to
    consult at all. It used to be checked *first*, which quietly pinned every
    model-less call to the Claude Code path and made ``default_model``
    unreachable — the briefing then failed on an Ollama-only machine and
    silently degraded to its deterministic template, forever.
    """
    if not model:
        model = _preferred_model(settings)
    if not model:
        if fallback_model:
            return ClaudeSDKAdapter(
                model=fallback_model,
                tool_namespace=tool_namespace,
                disallowed_tools=tuple(disallowed_tools),
            )
        raise AgentError("no model configured — register one under /system")

    entry = find_entry(settings.models, model)
    if entry is None:
        raise RuntimeError(f"unknown model {model!r} — register it under /system first")
    return adapter_for_entry(
        entry, tool_namespace=tool_namespace, disallowed_tools=disallowed_tools
    )


def resolve_run_target(
    settings: Any,
    model: str | None = None,
    *,
    tool_namespace: str = "argus",
    disallowed_tools: Sequence[str] = ("Bash", "Write", "Edit"),
    fallback_model: str | None = None,
) -> tuple[AgentAdapter, str]:
    """The adapter that will run, paired with the model id its rows belong to.

    Callers need both, and deriving them separately is how they drift. Chat and
    the planner each grew a private ``_resolve_model`` that answered the
    hardcoded Claude Code model whenever ``model`` was falsy, while
    :func:`resolve_adapter` — given the same falsy ``model`` — resolved through
    ``settings.default_model``. On a machine whose default is Ollama or
    DeepSeek the adapter ran that model and every usage and audit row was
    stamped ``claude-opus-4-8``, so the usage dashboard attributed a local
    model's tokens to Anthropic and the audit trail named the wrong reader.

    The label is the adapter's own ``model`` attribute, which the
    :class:`AgentAdapter` protocol requires. That is the provider-side id for
    the HTTP adapters (a ``groq-llama`` entry serving
    ``llama-3.3-70b-versatile`` labels rows with the latter) and the registry
    name on the Claude Code path, which is what those rows have always said.
    One resolution, one answer, no second opinion to disagree with.
    """
    adapter = resolve_adapter(
        settings,
        model,
        tool_namespace=tool_namespace,
        disallowed_tools=disallowed_tools,
        fallback_model=fallback_model,
    )
    return adapter, str(getattr(adapter, "model", "") or fallback_model or "")


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
            system_prompt=PROBE_SYSTEM,
            messages=[Message("user", PROBE_PROMPT)],
            tools=[spec],
            max_turns=2,
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
