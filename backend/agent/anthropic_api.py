"""Claude over the Anthropic Messages API, authenticated with an API key.

This is the piece that makes "no Claude Code required" true rather than
aspirational. :class:`~backend.agent.adapters.ClaudeSDKAdapter` needs the Claude
Code CLI installed and signed in (invariant I5); this adapter needs only a key
in the OS keyring, so a user who wants Claude's quality without installing a
developer CLI has a first-class path — and a user who wants neither still has
Ollama and the hosted open-weight providers.

The Messages API is *not* OpenAI-shaped: tools take ``input_schema`` rather
than nested ``function.parameters``, tool arguments stream as
``input_json_delta`` fragments against a content-block index, and results go
back as ``tool_result`` blocks inside a **user** message. Hence a second loop
rather than a coat of paint on the OpenAI one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.agent.adapters import (
    PROVIDER_ANTHROPIC_API,
    AgentError,
    AgentEvent,
    TextDelta,
    ToolSpec,
    ToolUsed,
    UsageReported,
    flatten_tool_result,
)

DEFAULT_ENDPOINT = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120.0


def to_anthropic_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render ToolSpecs in Messages-API format (``input_schema``, not nested)."""
    return [
        {"name": spec.name, "description": spec.description, "input_schema": spec.parameters}
        for spec in tools
    ]


class _BlockBuffer:
    """Accumulates streamed content blocks by index.

    ``tool_use`` blocks arrive as a start event carrying id and name, then a
    run of ``input_json_delta`` fragments that must be concatenated in order
    before they parse as JSON.
    """

    def __init__(self) -> None:
        self._blocks: dict[int, dict[str, Any]] = {}

    def start(self, index: int, block: dict[str, Any]) -> None:
        if block.get("type") != "tool_use":
            return
        self._blocks[index] = {
            "id": str(block.get("id") or f"toolu_{index}"),
            "name": str(block.get("name") or ""),
            "partial_json": "",
        }

    def delta(self, index: int, delta: dict[str, Any]) -> None:
        block = self._blocks.get(index)
        if block is not None and delta.get("type") == "input_json_delta":
            block["partial_json"] += str(delta.get("partial_json") or "")

    def finish(self) -> list[dict[str, Any]]:
        """Completed tool_use blocks in index order, inputs parsed."""
        calls = []
        for index in sorted(self._blocks):
            block = self._blocks[index]
            if not block["name"]:
                continue
            calls.append(
                {"id": block["id"], "name": block["name"], "input": _parse(block["partial_json"])}
            )
        return calls


def _parse(raw: str) -> dict[str, Any]:
    """Parse accumulated input JSON; an empty dict lets the handler complain."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class AnthropicAPIAdapter:
    """Runs the chat+tool-calling loop against the Anthropic Messages API."""

    model: str
    api_key: str
    endpoint: str = DEFAULT_ENDPOINT
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = None
    provider: str = field(default=PROVIDER_ANTHROPIC_API, init=False)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
                "Content-Type": "application/json",
            },
        )

    async def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        by_name = {spec.name: spec for spec in tools}
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        url = f"{self.endpoint.rstrip('/')}/messages"

        async with self._client() as client:
            for _turn in range(max(1, max_turns)):
                payload: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "messages": messages,
                    "stream": True,
                }
                if system_prompt:
                    payload["system"] = system_prompt
                if tools:
                    payload["tools"] = to_anthropic_tools(tools)

                text_parts: list[str] = []
                buffer = _BlockBuffer()
                async for event in self._stream_turn(
                    client, url, payload, text_parts, buffer, totals
                ):
                    yield event

                calls = buffer.finish()
                if not calls:
                    break

                assistant: list[dict[str, Any]] = []
                if "".join(text_parts):
                    assistant.append({"type": "text", "text": "".join(text_parts)})
                assistant.extend(
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                    for c in calls
                )
                messages.append({"role": "assistant", "content": assistant})

                # Results go back as a *user* message of tool_result blocks —
                # the Messages API has no dedicated tool role.
                results = []
                for call in calls:
                    yield ToolUsed(call["name"])
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": await _dispatch(by_name, call),
                        }
                    )
                messages.append({"role": "user", "content": results})

        yield UsageReported(**totals)

    async def _stream_turn(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        text_parts: list[str],
        buffer: _BlockBuffer,
        totals: dict[str, int],
    ) -> AsyncIterator[AgentEvent]:
        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                raise AgentError(await _error_detail(response))
            async for line in response.aiter_lines():
                event = _sse_payload(line)
                if event is None:
                    continue
                kind = event.get("type")

                if kind == "message_start":
                    _add_usage(totals, (event.get("message") or {}).get("usage"))
                elif kind == "content_block_start":
                    buffer.start(int(event.get("index") or 0), event.get("content_block") or {})
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        text_parts.append(str(delta["text"]))
                        yield TextDelta(str(delta["text"]))
                    else:
                        buffer.delta(int(event.get("index") or 0), delta)
                elif kind == "message_delta":
                    _add_usage(totals, event.get("usage"))
                elif kind == "error":
                    detail = (event.get("error") or {}).get("message") or "stream error"
                    raise AgentError(f"the model endpoint errored mid-stream: {detail}")


def _add_usage(totals: dict[str, int], usage: Any) -> None:
    """Fold one usage report into the running totals (both events carry partials)."""
    if not isinstance(usage, dict):
        return
    for key in totals:
        totals[key] += int(usage.get(key) or 0)


def _sse_payload(line: str) -> dict[str, Any] | None:
    """Decode one SSE ``data:`` line; ``event:`` lines and blanks are skipped."""
    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None
    data = stripped[len("data:") :].strip()
    if not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _dispatch(by_name: dict[str, ToolSpec], call: dict[str, Any]) -> str:
    """Run one tool call; failures come back as text the model can recover from."""
    spec = by_name.get(call["name"])
    if spec is None:
        return f"error: unknown tool {call['name']!r}"
    try:
        result = await spec.handler(call["input"])
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
        return f"error: {exc}"
    return flatten_tool_result(result)


async def _error_detail(response: httpx.Response) -> str:
    """Turn an Anthropic error response into something a user can act on."""
    await response.aread()
    detail = response.text.strip()
    try:
        error = response.json().get("error")
        if isinstance(error, dict) and error.get("message"):
            detail = str(error["message"])
    except Exception:  # noqa: BLE001 - fall back to the raw body
        pass
    if response.status_code in (401, 403):
        detail = f"{detail or 'unauthorized'} — check the Anthropic API key"
    elif response.status_code == 429:
        detail = f"{detail or 'rate limited'} — the key's rate limit or credit balance"
    return f"{response.status_code} from the Anthropic API: {detail}"


async def list_models(
    api_key: str,
    endpoint: str = DEFAULT_ENDPOINT,
    *,
    timeout: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Model ids the key can reach, so the UI can offer a list, not a text box."""
    async with httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
    ) as client:
        response = await client.get(f"{endpoint.rstrip('/')}/models")
        if response.status_code >= 400:
            raise AgentError(await _error_detail(response))
        payload = response.json()
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return sorted(
        {str(entry["id"]) for entry in entries if isinstance(entry, dict) and entry.get("id")}
    )
