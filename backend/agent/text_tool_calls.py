"""Recover tool calls a model wrote as *text* instead of as structured calls.

Every adapter in this package reads tool calls from the provider's structured
channel — ``delta.tool_calls`` (OpenAI), ``input_json_delta`` blocks
(Anthropic), ``functionCall`` parts (Gemini). That is the only channel they
read, and text is streamed to the browser the moment it arrives.

Small local models break that assumption routinely. When a chat template fails
to parse a model's output back into the structured field — the normal failure
mode for the 1B-7B models ``backend.agent.model_catalog`` offers as one-click
installs — the call arrives in ``content`` instead, as one of a handful of
well-known envelopes. With no sieve in front of it, that string is an ordinary
text delta: it is streamed to the user, syntax-highlighted as JSON, and
persisted to ``chat_messages``, where it survives a reload. A product review
caught exactly that, with a ``search_vault`` call shown to the user as the
assistant's reply.

This module is the sieve. Text is withheld only while it could still resolve
into an envelope, and a match is *claimed* only when the parsed name is a tool
the agent actually has. Two rules keep ordinary prose out of its way:

1. It only engages at the start of a message (or directly after an envelope it
   already claimed). A model that has been answering in prose for two
   paragraphs and then writes a JSON code block is writing a code block.
2. An unrecognised name is not a tool call. ``{"name": "Alice"}`` at the top of
   an answer is text, and gets flushed as text.

Neither rule can be relaxed without the sieve starting to eat legitimate
answers, which is a worse failure than the one it fixes.
"""

from __future__ import annotations

import json
from collections.abc import Container
from typing import Any

#: Envelope openers, longest-first so ``startswith`` checks never shadow.
#: ``<tool_call>`` is Hermes/Qwen, ``[TOOL_CALLS]`` is Mistral, the fences are
#: what a model reaches for when it has been told to "output JSON", and a bare
#: brace is every model that simply printed the call.
_MARKERS = ("<tool_call>", "[TOOL_CALLS]", "```json", "```")

#: Closers that pair with an opener, so the trailing token is consumed rather
#: than flushed to the user as a stray ``</tool_call>``.
_CLOSERS = {"<tool_call>": "</tool_call>", "```json": "```", "```": "```"}

#: Upper bound on withheld text. A model that opens a brace and then writes an
#: essay must not have that essay held hostage waiting for a close that never
#: comes — past this, the sieve concedes and flushes.
MAX_HELD_CHARS = 8192

#: The chat agent's built-in belt. Named here rather than in
#: ``backend.agent.runtime`` so the chat router can reach them without
#: importing the agent — that module pulls chromadb and the embedding stack in
#: behind it, and the app is required to boot without either.
BUILTIN_CHAT_TOOL_NAMES = frozenset({"search_vault", "read_note", "list_notes", "list_tasks"})

#: Registered n8n automations become one tool each, so their names are open-ended.
AUTOMATION_TOOL_PREFIX = "run_automation_"


def is_chat_tool_name(name: str) -> bool:
    """Whether ``name`` is a tool the chat agent could actually have called."""
    return name in BUILTIN_CHAT_TOOL_NAMES or name.startswith(AUTOMATION_TOOL_PREFIX)


def is_only_a_tool_call(text: str) -> bool:
    """Whether ``text`` is nothing but a tool-call envelope for a known tool.

    The last line of defence, used before an assistant turn is persisted. The
    sieve above stops a printed call reaching the browser; this stops one that
    slipped past an envelope shape nobody has seen yet from being *stored* and
    replayed on every reload.

    Deliberately strict on both counts — the whole message, and a name the
    agent really has. "Give me that as JSON" is a reasonable request, and an
    answer that is entirely a JSON document must survive untouched.
    """
    body = text.strip()
    if not body:
        return False
    sieve = TextToolCallSieve(_KnownChatTools())
    remainder = sieve.feed(body) + sieve.finish()
    return sieve.claimed and not remainder.strip()


class _KnownChatTools:
    """``in`` over the chat agent's open-ended tool namespace."""

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and is_chat_tool_name(name)


#: Keys that have carried the tool's name across the envelopes seen in the wild.
_NAME_KEYS = ("name", "tool", "tool_name", "function_name", "recipient_name")
#: ...and the ones that have carried its arguments.
_ARG_KEYS = ("arguments", "parameters", "args", "input", "parameter_values")


def _is_prefix_of_any_marker(text: str) -> bool:
    """True when ``text`` is a partial marker — more chunks may complete it.

    Streaming splits ``<tool_call>`` across chunks as readily as anything else,
    so ``<to`` has to be held rather than flushed: flushing it is the whole bug.
    """
    return any(marker.startswith(text) for marker in _MARKERS if len(text) < len(marker))


def _scan_json_value(text: str, start: int) -> int | None:
    """End index (exclusive) of the JSON object/array beginning at ``start``.

    ``None`` means "not finished yet" — the caller must wait for more text.
    Brace counting is string-aware, because a query like ``{"q": "a } b"}``
    would otherwise close the object early and produce a truncated, unparseable
    call.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _as_arguments(raw: Any) -> dict[str, Any] | None:
    """Normalise an envelope's argument payload to a dict.

    Models emit arguments both as a nested object and as a JSON *string* (the
    shape the OpenAI wire format uses), and both are common enough that
    handling only one leaves half the leaks in place.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _extract_call(payload: Any) -> tuple[str, dict[str, Any]] | None:
    """Pull ``(name, arguments)`` out of one parsed envelope, or ``None``.

    Deliberately total: an envelope this does not recognise is prose as far as
    the sieve is concerned, and prose is what the caller then flushes.
    """
    if not isinstance(payload, dict):
        return None
    # {"type": "function", "function": {"name": ..., "arguments": ...}} and the
    # bare {"function": {...}} it is usually abbreviated to.
    nested = payload.get("function")
    if isinstance(nested, dict):
        return _extract_call(nested)

    name = next((payload[key] for key in _NAME_KEYS if isinstance(payload.get(key), str)), None)
    if not name:
        return None
    raw_args = next((payload[key] for key in _ARG_KEYS if key in payload), None)
    arguments = _as_arguments(raw_args)
    if arguments is None:
        return None
    return str(name), arguments


def _extract_calls(payload: Any) -> list[tuple[str, dict[str, Any]]] | None:
    """``_extract_call`` over one envelope or a list of them (Mistral sends a list).

    All-or-nothing: a list where one entry fails to parse is not a tool call,
    because claiming half of it would silently drop work the model asked for.
    """
    if isinstance(payload, list):
        extracted = [_extract_call(entry) for entry in payload]
        if not extracted or any(call is None for call in extracted):
            return None
        return [call for call in extracted if call is not None]
    single = _extract_call(payload)
    return [single] if single else None


class TextToolCallSieve:
    """Filters text deltas, holding back anything that is really a tool call.

    Feed every content delta through :meth:`feed` and stream what it returns.
    Call :meth:`finish` when the completion ends to flush whatever was still
    being weighed up, and read :attr:`calls` for what was claimed.
    """

    def __init__(self, known_tools: Container[str], *, id_prefix: str = "text") -> None:
        self._known = known_tools
        self._id_prefix = id_prefix
        self._buffer = ""
        #: Once prose is confirmed, the sieve stops looking entirely — rule 1.
        self._passthrough = False
        self.calls: list[dict[str, Any]] = []

    @property
    def claimed(self) -> bool:
        """Whether anything was pulled out of the text channel this turn."""
        return bool(self.calls)

    def feed(self, chunk: str) -> str:
        """Absorb one content delta; return the text that is safe to emit now."""
        if self._passthrough:
            return chunk
        self._buffer += chunk
        return self._drain()

    def finish(self) -> str:
        """End of completion: flush whatever is still held.

        An envelope that never closed is text — the model was cut off, and
        showing a truncated answer beats showing nothing at all.
        """
        self._passthrough = True
        held, self._buffer = self._buffer, ""
        return held

    def _drain(self) -> str:
        """Consume as much of the buffer as can be decided, emitting the prose."""
        emitted: list[str] = []
        while self._buffer:
            leading = len(self._buffer) - len(self._buffer.lstrip())
            body = self._buffer[leading:]
            if not body:
                # Nothing but whitespace so far. Hold it: emitting it now would
                # put a stray newline in front of an answer whose first real
                # character turns out to open a tool call we then swallow.
                break
            consumed = self._try_envelope(body)
            if consumed is None:
                # Still undecided — wait for more, unless the model has clearly
                # moved on and we are just hoarding an answer.
                if len(self._buffer) > MAX_HELD_CHARS:
                    self._passthrough = True
                    emitted.append(self._buffer)
                    self._buffer = ""
                break
            if consumed == 0:
                # Confirmed prose. Rule 1: stop looking for the rest of the turn.
                self._passthrough = True
                emitted.append(self._buffer)
                self._buffer = ""
                break
            self._buffer = self._buffer[leading + consumed :]
        return "".join(emitted)

    def _try_envelope(self, body: str) -> int | None:
        """Characters of ``body`` consumed by a claimed call.

        ``0`` means "this is prose", ``None`` means "cannot tell yet".
        """
        scanned = self._scan(body)
        if scanned is None:
            return None
        consumed, found = scanned
        if not consumed:
            return 0
        # Recording happens here and only here. Scanning is deliberately free
        # of side effects: a partially-arrived envelope is re-scanned from the
        # same buffer offset on every later chunk, and an earlier version that
        # appended during the scan recorded the same call once per chunk.
        for name, arguments in found:
            self.calls.append(
                {
                    "id": f"{self._id_prefix}_{len(self.calls)}",
                    "name": name,
                    # The same arguments under all three names the adapters
                    # read them by: `arguments` as a JSON string is the OpenAI
                    # wire shape, `input` is Anthropic's `tool_use` block, and
                    # `args` is Gemini's `functionCall`. Carrying all three
                    # here means a claimed call is a drop-in for a structured
                    # one in every loop, with no per-adapter conversion to
                    # forget.
                    "arguments": json.dumps(arguments),
                    "input": arguments,
                    "args": arguments,
                }
            )
        return consumed

    def _scan(self, body: str) -> tuple[int, list[tuple[str, dict[str, Any]]]] | None:
        """Side-effect-free look at ``body``: how much is a call, and which.

        ``(0, [])`` is prose; ``None`` is "not enough text yet".
        """
        marker = next((m for m in _MARKERS if body.startswith(m)), None)
        if marker is None:
            if body[0] in "{[":
                return self._scan_json(body, start=0)
            # A partial marker still has a future; anything else does not.
            return None if _is_prefix_of_any_marker(body) else (0, [])

        rest = body[len(marker) :]
        stripped = rest.lstrip()
        if not stripped:
            return None
        if stripped[0] not in "{[":
            # A ```json block opening on something that is not a call, or a
            # fence around ordinary code. Prose.
            return (0, [])
        scanned = self._scan_json(body, start=len(marker) + (len(rest) - len(stripped)))
        if scanned is None:
            return None
        consumed, found = scanned
        if not consumed:
            return (0, [])
        closer = _CLOSERS.get(marker)
        if closer is None:
            return (consumed, found)
        tail = body[consumed:]
        stripped_tail = tail.lstrip()
        if stripped_tail.startswith(closer):
            return (consumed + (len(tail) - len(stripped_tail)) + len(closer), found)
        if not stripped_tail or closer.startswith(stripped_tail):
            # The closer is still arriving. Keep holding rather than emit the
            # call's own closing token as if it were part of the answer.
            return None
        return (consumed, found)

    def _scan_json(
        self, body: str, *, start: int
    ) -> tuple[int, list[tuple[str, dict[str, Any]]]] | None:
        """Scan a JSON object/array sitting directly in the text channel."""
        end = _scan_json_value(body, start)
        if end is None:
            return None
        try:
            payload = json.loads(body[start:end])
        except json.JSONDecodeError:
            return (0, [])
        extracted = _extract_calls(payload)
        # Rule 2: a name the agent cannot dispatch is not a tool call, however
        # much it looks like one.
        if extracted is None or not all(name in self._known for name, _ in extracted):
            return (0, [])
        return (end, extracted)
