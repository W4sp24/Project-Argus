"""The OpenAI-compatible adapter — one adapter for every non-Claude backend.

Local Ollama, hosted open-weight providers (Groq, Together, Fireworks,
DeepInfra, OpenRouter), and Codex's and Gemini's own APIs are all the same
surface: ``POST {endpoint}/chat/completions`` with ``tools`` and streamed
``tool_calls``. One adapter covers them all; the only thing that changes is the
base URL and whether an API key rides along.

Unlike ``claude-agent-sdk``, a chat-completions endpoint has no orchestrator —
it returns tool calls and stops. This module runs that loop by hand: stream a
completion, accumulate ``tool_calls`` **by index** (they arrive fragmented
across chunks), dispatch each to its :class:`~backend.agent.adapters.ToolSpec`
handler, append the results, and go round again until the model answers with
text or ``max_turns`` is spent.

Uses ``httpx`` directly rather than the ``openai`` client: httpx is already a
dependency, adding nothing new to the frozen desktop backend (where PyInstaller
breaks dependencies silently), and ``httpx.MockTransport`` makes the whole loop
testable without a network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from backend.agent.adapters import (
    PROVIDER_OPENAI_COMPAT,
    AgentError,
    AgentEvent,
    Message,
    Notice,
    TextDelta,
    ToolFinished,
    ToolSpec,
    ToolStarted,
    UsageReported,
    flatten_tool_result,
    is_local_endpoint,
    require_user_turn,
    summarize_tool_result,
)

DEFAULT_TIMEOUT_SECONDS = 120.0


class _StreamOptionsError(AgentError):
    """The server refused ``stream_options``; retry the same turn without it."""


def normalize_base_url(endpoint: str) -> str:
    """Normalize a user-entered endpoint into an OpenAI-compatible base URL.

    A bare origin (``http://localhost:11434``) is never a valid OpenAI-compatible
    base — every such server mounts the API under a versioned path — so an empty
    path is filled in with ``/v1``. This is the single most common thing a
    non-developer gets wrong when pointing Argus at Ollama.
    """
    parsed = urlparse(endpoint.strip())
    if not parsed.scheme or not parsed.netloc:
        raise AgentError(f"{endpoint!r} is not an http(s) URL")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def chat_completions_url(endpoint: str) -> str:
    """``{base}/chat/completions``, tolerating a base that already includes it."""
    base = normalize_base_url(endpoint)
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def models_url(endpoint: str) -> str:
    """``{base}/models`` — used to list what an endpoint actually serves."""
    return f"{normalize_base_url(endpoint)}/models"


def to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render ToolSpecs in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in tools
    ]


class _ToolCallBuffer:
    """Accumulates streamed ``tool_calls`` fragments, keyed by index.

    Providers split one tool call across many chunks: the id and name arrive
    once, then ``arguments`` dribbles in as JSON text. The ``index`` field is
    the only stable join key — ids are absent from later fragments — so
    anything that accumulates by id or by arrival order corrupts multi-tool
    turns.
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def add(self, fragment: dict[str, Any]) -> None:
        index = int(fragment.get("index") or 0)
        call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if fragment.get("id"):
            call["id"] = str(fragment["id"])
        function = fragment.get("function") or {}
        if function.get("name"):
            call["name"] = str(function["name"])
        if function.get("arguments"):
            call["arguments"] += str(function["arguments"])

    def finish(self) -> list[dict[str, str]]:
        """Completed calls in index order, each with a usable id."""
        finished = []
        for index in sorted(self._calls):
            call = dict(self._calls[index])
            # Some servers omit ids entirely; the loop still needs to correlate
            # each tool result back to its call, so synthesize a stable one.
            call["id"] = call["id"] or f"call_{index}"
            finished.append(call)
        return [call for call in finished if call["name"]]

    def __bool__(self) -> bool:
        return any(call.get("name") for call in self._calls.values())


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse a tool call's argument JSON, tolerating the empty-string case.

    Small models sometimes emit ``""`` or malformed JSON for a no-argument
    tool. An empty dict lets the handler apply its own validation and return a
    readable error the model can recover from, which beats aborting the turn.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class OpenAICompatAdapter:
    """Runs the chat+tool-calling loop against any OpenAI-compatible endpoint."""

    model: str
    endpoint: str
    api_key: str | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = None
    provider: str = field(default=PROVIDER_OPENAI_COMPAT, init=False)
    # See AnthropicAPIAdapter.partial_usage: a consumer that walks away
    # mid-stream never sees the final UsageReported, but the tokens were spent.
    _live_usage: dict[str, int] | None = field(default=None, init=False, repr=False)
    _live_turn: dict[str, int] | None = field(default=None, init=False, repr=False)
    # Whether to ask for token counts via `stream_options`. See __post_init__.
    _ask_for_usage: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Decide once whether this endpoint gets asked for token counts.

        Several OpenAI-compatible servers reject unknown request fields
        outright, which is why this payload stayed minimal for a long time. The
        cost of that caution was that hosted providers which only report usage
        when asked — DeepSeek among them — recorded **zero tokens** for every
        turn, so the usage dashboard quietly under-reported the models that
        actually cost money.

        Local endpoints keep the old payload byte-for-byte: Ollama and whatever
        else someone points at 127.0.0.1 are the servers most likely to be
        strict, and their tokens are free anyway. Anything off-machine is a
        commercial API whose whole business is counting tokens, so it is asked.
        A server that still says no is handled in ``run``.
        """
        self._ask_for_usage = not is_local_endpoint(self.endpoint)

    def partial_usage(self) -> UsageReported | None:
        """Whatever has been counted so far, for a stream that ended early.

        Reads the banked turns *and* the turn still in flight. `_stream_turn`
        folds its own counters in a `finally`, but when the outer generator is
        closed the inner one is only finalized whenever the GC gets to it — so
        relying on that alone loses exactly the turn the user walked away from.
        Same reasoning, same fix, as AnthropicAPIAdapter.partial_usage.
        """
        if self._live_usage is None:
            return None
        live = self._live_turn or {}
        return UsageReported(
            input_tokens=self._live_usage["input_tokens"] + live.get("input_tokens", 0),
            output_tokens=self._live_usage["output_tokens"] + live.get("output_tokens", 0),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, headers=self._headers()
        )

    async def run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        require_user_turn(messages)
        conversation: list[dict[str, Any]] = []
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})
        conversation.extend({"role": m.role, "content": m.text} for m in messages)

        by_name = {spec.name: spec for spec in tools}
        totals = {"input_tokens": 0, "output_tokens": 0}
        self._live_usage = totals
        url = chat_completions_url(self.endpoint)

        total_turns = max(1, max_turns)
        hit_limit = False

        async with self._client() as client:
            for turn in range(total_turns):
                # Reaching the last turn means every earlier one was spent on
                # tool calls, so the model is out of room to look anything else
                # up. Left on "auto" it would spend this turn on another call
                # whose result the loop exits before reading — the user gets a
                # tool trace and then nothing. Forcing text spends the last turn
                # answering from what was already gathered.
                final = turn == total_turns - 1
                hit_limit = hit_limit or (final and turn > 0 and bool(tools))
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": conversation,
                    "stream": True,
                }
                if tools:
                    payload["tools"] = to_openai_tools(tools)
                    payload["tool_choice"] = "none" if final else "auto"
                if self._ask_for_usage:
                    payload["stream_options"] = {"include_usage": True}

                text_parts: list[str] = []
                buffer = _ToolCallBuffer()
                try:
                    async for event in self._stream_turn(
                        client, url, payload, text_parts, buffer, totals
                    ):
                        yield event
                except _StreamOptionsError:
                    # A strict server we guessed wrong about. Nothing has been
                    # read yet (the status check precedes the body), so the
                    # turn can simply be re-sent without the field — and every
                    # later turn skips it too rather than paying the round trip
                    # again.
                    self._ask_for_usage = False
                    payload.pop("stream_options", None)
                    async for event in self._stream_turn(
                        client, url, payload, text_parts, buffer, totals
                    ):
                        yield event

                calls = buffer.finish()
                if not calls:
                    break

                conversation.append(
                    {
                        "role": "assistant",
                        "content": "".join(text_parts) or None,
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": call["arguments"] or "{}",
                                },
                            }
                            for call in calls
                        ],
                    }
                )

                for call in calls:
                    args = parse_tool_arguments(call["arguments"])
                    yield ToolStarted(call_id=call["id"], name=call["name"], args=args)
                    result_text = await _dispatch(by_name, call)
                    yield ToolFinished(
                        call_id=call["id"],
                        name=call["name"],
                        summary=summarize_tool_result(
                            by_name.get(call["name"]), call["name"], args, result_text
                        ),
                    )
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": result_text,
                        }
                    )

        if hit_limit:
            yield Notice(
                kind="turn_limit",
                detail=(
                    f"reached the {total_turns}-step limit for this turn — "
                    "the answer may be incomplete"
                ),
            )
        yield UsageReported(
            input_tokens=totals["input_tokens"], output_tokens=totals["output_tokens"]
        )

    async def _stream_turn(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        text_parts: list[str],
        buffer: _ToolCallBuffer,
        totals: dict[str, int],
    ) -> AsyncIterator[AgentEvent]:
        """Stream one completion, filling ``text_parts``/``buffer``/``totals``."""
        # This completion's own counters, folded into ``totals`` when it ends.
        # Usage arrives cumulative within one completion, so it is assigned
        # here and summed only across turns — the same split
        # AnthropicAPIAdapter makes, and the reason its accounting has never
        # multiplied on a server that repeats the totals per chunk.
        turn_usage = {"input_tokens": 0, "output_tokens": 0}
        self._live_turn = turn_usage
        try:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    detail = await _error_detail(response, self.model)
                    if response.status_code == 400 and "stream_options" in detail:
                        raise _StreamOptionsError(detail)
                    raise AgentError(detail)
                async for line in response.aiter_lines():
                    chunk = _sse_payload(line)
                    if chunk is None:
                        continue

                    # Read from whatever chunk carries it: asked-for or not, a
                    # server may volunteer usage, and token logging is
                    # best-effort by design (see backend/usage.py).
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        turn_usage["input_tokens"] = int(usage.get("prompt_tokens") or 0)
                        turn_usage["output_tokens"] = int(usage.get("completion_tokens") or 0)

                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            text_parts.append(str(content))
                            yield TextDelta(str(content))
                        for fragment in delta.get("tool_calls") or []:
                            buffer.add(fragment)
        finally:
            # Even a cancelled or failed turn already cost the user tokens, so
            # bank whatever the server reported before we stopped reading.
            for key in totals:
                totals[key] += turn_usage[key]
                turn_usage[key] = 0


def _sse_payload(line: str) -> dict[str, Any] | None:
    """Decode one SSE line into a chunk, or None for keep-alives and [DONE]."""
    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None
    data = stripped[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return None
    return chunk if isinstance(chunk, dict) else None


async def _dispatch(by_name: dict[str, ToolSpec], call: dict[str, str]) -> str:
    """Run one tool call, returning text the model can read.

    Handler failures come back as text rather than raising: the model can
    recover from "error: no note at X" on the next turn, whereas an exception
    kills the whole answer.
    """
    spec = by_name.get(call["name"])
    if spec is None:
        return f"error: unknown tool {call['name']!r}"
    try:
        result = await spec.handler(parse_tool_arguments(call["arguments"]))
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
        return f"error: {exc}"
    return flatten_tool_result(result)


async def _error_detail(response: httpx.Response, model: str) -> str:
    """Turn a provider error response into something a user can act on."""
    await response.aread()
    detail = response.text.strip()
    try:
        payload = response.json()
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            detail = str(error["message"])
        elif isinstance(error, str):
            detail = error
    except Exception:  # noqa: BLE001 - fall back to the raw body
        pass
    if response.status_code == 404:
        detail = f"{detail or 'not found'} (is the model {model!r} pulled on that endpoint?)"
    elif response.status_code in (401, 403):
        detail = f"{detail or 'unauthorized'} — check the API key"
    return f"{response.status_code} from the model endpoint: {detail}"


async def list_models(
    endpoint: str,
    api_key: str | None = None,
    *,
    timeout: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Model ids an endpoint serves, so the UI can offer a list, not a text box."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers) as client:
        response = await client.get(models_url(endpoint))
        if response.status_code >= 400:
            raise AgentError(await _error_detail(response, ""))
        payload = response.json()
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return sorted(
        {str(entry["id"]) for entry in entries if isinstance(entry, dict) and entry.get("id")}
    )
