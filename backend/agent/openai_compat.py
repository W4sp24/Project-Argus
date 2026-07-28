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
    TextDelta,
    ToolSpec,
    ToolUsed,
    UsageReported,
    flatten_tool_result,
)

DEFAULT_TIMEOUT_SECONDS = 120.0


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
        user_message: str,
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        by_name = {spec.name: spec for spec in tools}
        totals = {"input_tokens": 0, "output_tokens": 0}
        url = chat_completions_url(self.endpoint)

        async with self._client() as client:
            for _turn in range(max(1, max_turns)):
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                }
                if tools:
                    payload["tools"] = to_openai_tools(tools)
                    payload["tool_choice"] = "auto"

                text_parts: list[str] = []
                buffer = _ToolCallBuffer()
                async for event in self._stream_turn(
                    client, url, payload, text_parts, buffer, totals
                ):
                    yield event

                calls = buffer.finish()
                if not calls:
                    break

                messages.append(
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
                    yield ToolUsed(call["name"])
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": await _dispatch(by_name, call),
                        }
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
        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                raise AgentError(await _error_detail(response, self.model))
            async for line in response.aiter_lines():
                chunk = _sse_payload(line)
                if chunk is None:
                    continue

                # Usage is read opportunistically rather than requested via
                # `stream_options`: several OpenAI-compatible servers reject
                # unknown request fields outright, and token logging is
                # best-effort by design (see backend/usage.py) while broad
                # provider compatibility is not.
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    totals["input_tokens"] += int(usage.get("prompt_tokens") or 0)
                    totals["output_tokens"] += int(usage.get("completion_tokens") or 0)

                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        text_parts.append(str(content))
                        yield TextDelta(str(content))
                    for fragment in delta.get("tool_calls") or []:
                        buffer.add(fragment)


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
