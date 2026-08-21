"""Tests for the text-channel tool-call sieve.

The bug this guards against was reported from a real session: a local model
emitted a ``search_vault`` call as prose and the raw JSON was shown to the user
as the assistant's reply, followed by an unexplained "That's not in your
notes."

Two halves matter equally and are tested as such. The sieve must *catch* every
envelope shape a small model actually emits, and it must *not* catch prose —
a sieve that swallows a legitimate answer containing JSON is a worse bug than
the one it replaces.
"""

from __future__ import annotations

import json

import pytest

from backend.agent.text_tool_calls import (
    MAX_HELD_CHARS,
    TextToolCallSieve,
    is_chat_tool_name,
    is_only_a_tool_call,
)

KNOWN = ("search_vault", "read_note", "list_tasks")


def run(*chunks: str) -> tuple[str, list[dict]]:
    """Feed chunks through a sieve; return the visible text and claimed calls."""
    sieve = TextToolCallSieve(KNOWN)
    visible = "".join(sieve.feed(chunk) for chunk in chunks)
    visible += sieve.finish()
    return visible, sieve.calls


def only_call(calls: list[dict]) -> tuple[str, dict]:
    assert len(calls) == 1, calls
    return calls[0]["name"], json.loads(calls[0]["arguments"])


# --- envelopes that must be claimed ------------------------------------------


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("bare object", '{"name": "search_vault", "arguments": {"query": "Argus"}}'),
        ("hermes/qwen", '<tool_call>{"name": "search_vault",'
                        ' "arguments": {"query": "Argus"}}</tool_call>'),
        ("mistral", '[TOOL_CALLS] [{"name": "search_vault", "arguments": {"query": "Argus"}}]'),
        ("fenced json", '```json\n{"name": "search_vault", "arguments": {"query": "Argus"}}\n```'),
        ("bare fence", '```\n{"name": "search_vault", "arguments": {"query": "Argus"}}\n```'),
        ("parameters key", '{"name": "search_vault", "parameters": {"query": "Argus"}}'),
        ("tool key", '{"tool": "search_vault", "args": {"query": "Argus"}}'),
        ("nested function", '{"type": "function", "function": {"name": "search_vault",'
                            ' "arguments": {"query": "Argus"}}}'),
        ("arguments as a json string", '{"name": "search_vault", "arguments":'
                                       ' "{\\"query\\": \\"Argus\\"}"}'),
        ("leading whitespace", '\n\n  {"name": "search_vault", "arguments": {"query": "Argus"}}'),
    ],
)
def test_an_envelope_is_claimed_and_never_shown(label: str, payload: str) -> None:
    visible, calls = run(payload)
    assert visible == "", f"{label} leaked to the user"
    assert only_call(calls) == ("search_vault", {"query": "Argus"})


def test_a_call_split_across_chunks_is_still_claimed() -> None:
    """Streaming splits an envelope mid-token as readily as anything else.

    Character-by-character is the worst case and the one that would break a
    naive "does this chunk look like JSON?" check.
    """
    payload = '<tool_call>{"name": "read_note", "arguments": {"path": "a.md"}}</tool_call>'
    visible, calls = run(*payload)
    assert visible == ""
    assert only_call(calls) == ("read_note", {"path": "a.md"})


def test_a_tool_taking_no_arguments_is_claimed() -> None:
    visible, calls = run('{"name": "list_tasks", "arguments": {}}')
    assert visible == ""
    assert only_call(calls) == ("list_tasks", {})


def test_a_brace_inside_a_string_does_not_close_the_object_early() -> None:
    """Naive brace counting truncates this into unparseable JSON, which the
    sieve would then flush to the user as text — the original bug, restored."""
    visible, calls = run('{"name": "search_vault", "arguments": {"query": "a } b"}}')
    assert visible == ""
    assert only_call(calls) == ("search_vault", {"query": "a } b"})


def test_several_calls_in_one_list_are_all_claimed() -> None:
    visible, calls = run(
        '[TOOL_CALLS] [{"name": "search_vault", "arguments": {"query": "x"}},'
        ' {"name": "read_note", "arguments": {"path": "a.md"}}]'
    )
    assert visible == ""
    assert [call["name"] for call in calls] == ["search_vault", "read_note"]
    assert len({call["id"] for call in calls}) == 2, "ids must be unique to pair start/end frames"


# --- prose that must NOT be claimed -------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "You have two exams that week.",
        # An unknown name is rule 2: this is a record, not a call.
        '{"name": "Alice", "age": 30} is the record you asked about.',
        # Valid-looking but for a tool the agent does not have.
        '{"name": "delete_everything", "arguments": {}}',
        # Opens like an envelope and then is not JSON at all.
        "{ this was never JSON",
        # A fence around something that is not a call.
        "```json\n{\"totally\": \"ordinary data\"}\n```",
        # Markdown that merely begins with a bracket.
        "[a link](https://example.com) explains it.",
    ],
)
def test_prose_is_passed_through_untouched(prose: str) -> None:
    visible, calls = run(prose)
    assert visible == prose
    assert calls == []


def test_a_code_block_later_in_an_answer_is_never_claimed() -> None:
    """Rule 1. Once the model is demonstrably writing prose, the sieve stops
    looking — otherwise every answer that explains a tool call would lose it."""
    answer = (
        "Here is how the call is shaped:\n\n"
        '```json\n{"name": "search_vault", "arguments": {"query": "Argus"}}\n```\n'
        "Ask me anything else."
    )
    visible, calls = run(answer)
    assert visible == answer
    assert calls == []


def test_prose_following_a_claimed_call_still_reaches_the_user() -> None:
    """A model that calls and then narrates must keep the narration."""
    visible, calls = run(
        '{"name": "search_vault", "arguments": {"query": "Argus"}}\nLet me check that.'
    )
    assert only_call(calls) == ("search_vault", {"query": "Argus"})
    assert visible.strip() == "Let me check that."


def test_an_unclosed_envelope_is_flushed_rather_than_swallowed() -> None:
    """The model was cut off. A truncated answer beats a blank one."""
    partial = '{"name": "search_vault", "arguments": {"query": "Arg'
    visible, calls = run(partial)
    assert visible == partial
    assert calls == []


def test_a_very_long_hold_gives_up_rather_than_hoarding_the_answer() -> None:
    """A stray opening brace must not hold an entire essay hostage."""
    essay = "{" + "x" * (MAX_HELD_CHARS + 100)
    visible, calls = run(essay)
    assert visible == essay
    assert calls == []


def test_feeding_after_prose_is_confirmed_costs_nothing() -> None:
    sieve = TextToolCallSieve(KNOWN)
    assert sieve.feed("Plain prose. ") == "Plain prose. "
    # Rule 1 again, at the API level: later chunks are forwarded verbatim.
    assert sieve.feed('{"name": "search_vault", "arguments": {}}') == (
        '{"name": "search_vault", "arguments": {}}'
    )
    assert sieve.claimed is False


# --- the persistence net ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"name": "search_vault", "arguments": {"query": "Argus"}}',
        '<tool_call>{"name": "read_note", "arguments": {"path": "a.md"}}</tool_call>',
        '  {"name": "run_automation_plan", "arguments": {}}  ',
        '```json\n{"name": "list_tasks", "arguments": {}}\n```',
    ],
)
def test_a_message_that_is_only_a_tool_call_is_recognised(text: str) -> None:
    assert is_only_a_tool_call(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # A legitimate answer that happens to be entirely JSON. Stripping this
        # would be a worse bug than the leak it guards against.
        '{"totally": "an ordinary document"}',
        '[{"id": 1}, {"id": 2}]',
        # A real answer with a call at the front — the sieve handles this; the
        # net must not fire on it and throw the answer away.
        '{"name": "search_vault", "arguments": {}} Here is what I found.',
        "Here is the config:\n```json\n{\"a\": 1}\n```",
        "You have two exams that week.",
        "",
        "   ",
    ],
)
def test_ordinary_text_is_not_mistaken_for_a_bare_tool_call(text: str) -> None:
    assert is_only_a_tool_call(text) is False


def test_an_automation_tool_is_recognised_by_prefix() -> None:
    """Automation tool names are one-per-registered-workflow, so they cannot be
    enumerated the way the built-in belt can."""
    assert is_chat_tool_name("run_automation_meeting_prep") is True
    assert is_chat_tool_name("search_vault") is True
    assert is_chat_tool_name("delete_everything") is False
