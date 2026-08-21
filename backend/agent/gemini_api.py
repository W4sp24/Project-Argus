"""Gemini over Google's Generative Language API, authenticated with an API key.

Google also ships an OpenAI-compatible shim, and routing Gemini through
:class:`~backend.agent.openai_compat.OpenAICompatAdapter` would have cost no
code at all. It was rejected on one specific ground: the shim's streamed
``tool_calls`` fragments do not reliably carry an ``index``, and
``_ToolCallBuffer`` keys on exactly that field because ids are absent from
later fragments. Two tool calls in one turn would silently fold into one — the
worst possible failure for an agent whose citations come out of its tool trace.

The native API is a third shape again, different from both the OpenAI and the
Anthropic one:

* Turns are ``contents`` with roles ``user`` / ``model`` (not ``assistant``),
  and each is a list of ``parts``.
* The system prompt is its own top-level ``systemInstruction``, not a turn.
* Tools are ``functionDeclarations`` under a single ``tools`` entry, and the
  schema dialect is a **subset** of JSON Schema — see :func:`to_gemini_tools`.
* A tool call arrives as a complete ``functionCall`` part, already parsed, not
  as a run of JSON fragments — so there is no argument buffer here.
* Results go back as a ``user`` turn of ``functionResponse`` parts, the same
  "no dedicated tool role" shape the Messages API has.
* ``usageMetadata`` is cumulative per response, so it is assigned rather than
  added — the trap :func:`backend.agent.anthropic_api._apply_usage` documents.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.agent.adapters import (
    PROVIDER_GEMINI,
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
    require_user_turn,
    summarize_tool_result,
)
from backend.agent.text_tool_calls import TextToolCallSieve

DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT_SECONDS = 120.0

# Keys Gemini's function-declaration schema dialect understands. It is an
# OpenAPI 3.0 subset, not JSON Schema, and it rejects an unrecognised key
# outright with a 400 rather than ignoring it — so anything Argus might add to
# a ToolSpec later (``additionalProperties``, ``$defs``, ``examples``) has to be
# filtered here rather than discovered in production.
_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "properties",
        "required",
        "items",
    }
)


def _clean_schema(schema: Any) -> Any:
    """Strip a JSON Schema down to the subset Gemini accepts, recursively."""
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        elif key == "items":
            cleaned[key] = _clean_schema(value)
        else:
            cleaned[key] = value
    return cleaned


def to_gemini_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Render ToolSpecs as a single ``functionDeclarations`` entry.

    Two things differ from every other provider. Gemini takes one ``tools``
    element holding all declarations rather than one element per tool, and it
    **rejects a parameters object with no properties**: Argus's ``list_tasks``
    takes no arguments, so ``json_schema({})`` produces exactly the
    ``{"properties": {}}`` that earns a 400. Omitting ``parameters`` entirely is
    how a no-argument tool is declared here, and getting that wrong fails the
    whole request — every tool in the belt, not just the empty one — which
    reads from the outside as "Gemini will not call tools".
    """
    declarations = []
    for spec in tools:
        declaration: dict[str, Any] = {"name": spec.name, "description": spec.description}
        parameters = _clean_schema(spec.parameters)
        if isinstance(parameters, dict) and parameters.get("properties"):
            declaration["parameters"] = parameters
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}]


def to_gemini_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Render the conversation as ``contents``; ``assistant`` becomes ``model``."""
    return [
        {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.text}]}
        for m in messages
    ]


@dataclass
class GeminiAdapter:
    """Runs the chat+tool-calling loop against the Generative Language API."""

    model: str
    api_key: str
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = None
    provider: str = field(default=PROVIDER_GEMINI, init=False)
    # See AnthropicAPIAdapter.partial_usage: a consumer that walks away
    # mid-stream never sees the final UsageReported, but the tokens were spent.
    _live_usage: dict[str, int] | None = field(default=None, init=False, repr=False)
    _live_turn: dict[str, int] | None = field(default=None, init=False, repr=False)

    def partial_usage(self) -> UsageReported | None:
        """Whatever has been counted so far, for a stream that ended early.

        Reads the banked turns *and* the turn still in flight: closing the
        outer generator does not run the inner one's `finally` until the GC
        gets to it, which would lose exactly the turn the user walked away
        from. Same reasoning as AnthropicAPIAdapter.partial_usage.
        """
        if self._live_usage is None:
            return None
        live = self._live_turn or {}
        return UsageReported(
            input_tokens=self._live_usage["input_tokens"] + live.get("input_tokens", 0),
            output_tokens=self._live_usage["output_tokens"] + live.get("output_tokens", 0),
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
        )

    def _url(self) -> str:
        # `alt=sse` is what turns streamGenerateContent from a JSON array
        # dribbled out over the wire into real server-sent events; without it
        # the response is a single array that only parses once complete, which
        # would defeat streaming entirely.
        base = self.endpoint.rstrip("/")
        model = self.model if self.model.startswith("models/") else f"models/{self.model}"
        return f"{base}/{model}:streamGenerateContent?alt=sse"

    async def run(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        require_user_turn(messages)
        contents = to_gemini_contents(messages)
        by_name = {spec.name: spec for spec in tools}
        totals = {"input_tokens": 0, "output_tokens": 0}
        self._live_usage = totals
        url = self._url()

        total_turns = max(1, max_turns)
        hit_limit = False

        async with self._client() as client:
            for turn in range(total_turns):
                # See OpenAICompatAdapter.run: the last turn is forced to text
                # so it produces an answer rather than a tool result the loop
                # exits before reading.
                final = turn == total_turns - 1
                hit_limit = hit_limit or (final and turn > 0 and bool(tools))
                payload: dict[str, Any] = {"contents": contents}
                if system_prompt:
                    payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
                if tools:
                    payload["tools"] = to_gemini_tools(tools)
                    payload["toolConfig"] = {
                        "functionCallingConfig": {"mode": "NONE" if final else "AUTO"}
                    }

                text_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                # Parity with the other two adapters: a model that prints its
                # call as a text part instead of a functionCall must not have
                # that JSON shown to the user. See
                # backend.agent.text_tool_calls.
                sieve = TextToolCallSieve(by_name, id_prefix=f"text_{turn}")
                async for event in self._stream_turn(
                    client, url, payload, text_parts, calls, totals, sieve
                ):
                    yield event

                if not calls:
                    break

                model_parts: list[dict[str, Any]] = []
                if "".join(text_parts):
                    model_parts.append({"text": "".join(text_parts)})
                model_parts.extend(
                    {"functionCall": {"name": call["name"], "args": call["args"]}}
                    for call in calls
                )
                contents.append({"role": "model", "parts": model_parts})

                # Results go back as a *user* turn of functionResponse parts —
                # like the Messages API, Gemini has no dedicated tool role. The
                # response body must be an object, so the text is wrapped rather
                # than sent bare.
                responses = []
                for call in calls:
                    yield ToolStarted(
                        call_id=call["id"], name=call["name"], args=call["args"]
                    )
                    result_text = await _dispatch(by_name, call)
                    yield ToolFinished(
                        call_id=call["id"],
                        name=call["name"],
                        summary=summarize_tool_result(
                            by_name.get(call["name"]), call["name"], call["args"], result_text
                        ),
                    )
                    responses.append(
                        {
                            "functionResponse": {
                                "name": call["name"],
                                "response": {"result": result_text},
                            }
                        }
                    )
                contents.append({"role": "user", "parts": responses})

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
        calls: list[dict[str, Any]],
        totals: dict[str, int],
        sieve: TextToolCallSieve,
    ) -> AsyncIterator[AgentEvent]:
        """Stream one response, filling ``text_parts``/``calls``/``totals``.

        ``usageMetadata`` is cumulative per response, so this turn's counters
        are assigned here and folded into ``totals`` when the turn ends.
        """
        turn_usage = {"input_tokens": 0, "output_tokens": 0}
        self._live_turn = turn_usage
        try:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    raise AgentError(await _error_detail(response, self.model))
                async for line in response.aiter_lines():
                    chunk = _sse_payload(line)
                    if chunk is None:
                        continue

                    usage = chunk.get("usageMetadata")
                    if isinstance(usage, dict):
                        turn_usage["input_tokens"] = int(usage.get("promptTokenCount") or 0)
                        turn_usage["output_tokens"] = int(usage.get("candidatesTokenCount") or 0)

                    for candidate in chunk.get("candidates") or []:
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            text = part.get("text")
                            if text:
                                visible = sieve.feed(str(text))
                                if visible:
                                    text_parts.append(visible)
                                    yield TextDelta(visible)
                            function_call = part.get("functionCall")
                            if isinstance(function_call, dict) and function_call.get("name"):
                                args = function_call.get("args")
                                calls.append(
                                    {
                                        # Gemini sends no call ids at all, and
                                        # the tool trace pairs start/end events
                                        # by one, so synthesize a stable id.
                                        "id": f"gemini_{len(calls)}",
                                        "name": str(function_call["name"]),
                                        "args": args if isinstance(args, dict) else {},
                                    }
                                )
                # An envelope that never closed is text after all. Inside the
                # `async with` so it never runs on the error path.
                tail = sieve.finish()
                if tail:
                    text_parts.append(tail)
                    yield TextDelta(tail)
                # Gemini fills `calls` in place rather than returning them, so
                # the fallback is appended here rather than composed with `or`.
                if not calls:
                    calls.extend(sieve.calls)
        finally:
            # Even a cancelled or failed turn already cost the user tokens.
            for key in totals:
                totals[key] += turn_usage[key]
                turn_usage[key] = 0


def _sse_payload(line: str) -> dict[str, Any] | None:
    """Decode one SSE line into a chunk, or None for keep-alives."""
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


async def _dispatch(by_name: dict[str, ToolSpec], call: dict[str, Any]) -> str:
    """Run one tool call; failures come back as text the model can recover from."""
    spec = by_name.get(call["name"])
    if spec is None:
        known = ", ".join(sorted(by_name)) or "none"
        return f"error: unknown tool {call['name']!r} — available tools are {known}"
    try:
        result = await spec.handler(call["args"])
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
        return f"error: {exc}"
    return flatten_tool_result(result)


async def _error_detail(response: httpx.Response, model: str) -> str:
    """Turn a Gemini error response into something a user can act on."""
    await response.aread()
    detail = response.text.strip()
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("message"):
            detail = str(error["message"])
    except Exception:  # noqa: BLE001 - fall back to the raw body
        pass
    if response.status_code == 404:
        detail = f"{detail or 'not found'} (does the key have access to {model!r}?)"
    elif response.status_code in (400, 401, 403):
        detail = f"{detail or 'rejected'} — check the Google AI Studio API key"
    elif response.status_code == 429:
        detail = f"{detail or 'rate limited'} — the key's quota for this model"
    return f"{response.status_code} from the Gemini API: {detail}"


async def list_models(
    api_key: str,
    endpoint: str = DEFAULT_ENDPOINT,
    *,
    timeout: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Model ids the key can generate with, so the UI offers a list not a text box.

    Filtered to models advertising ``generateContent``: the same listing carries
    embedding and legacy models that would 404 the moment somebody picked one.
    The ``models/`` prefix is stripped because that is the form a user
    recognises and the form ``_url`` re-adds.
    """
    async with httpx.AsyncClient(
        timeout=timeout, transport=transport, headers={"x-goog-api-key": api_key}
    ) as client:
        response = await client.get(f"{endpoint.rstrip('/')}/models")
        if response.status_code >= 400:
            raise AgentError(await _error_detail(response, ""))
        payload = response.json()
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        methods = entry.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        names.add(str(entry["name"]).removeprefix("models/"))
    return sorted(names)
