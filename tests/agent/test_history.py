"""Tests for the conversation-history budget and serialization policy.

``test_serialize_history_of_one_message_is_verbatim`` is the load-bearing
test in this file: it names, explicitly, the compatibility guarantee that
keeps every pre-existing call site and every pre-existing test green after
adapters grew a message list instead of a bare string.
"""

from __future__ import annotations

from backend.agent.adapters import Message
from backend.agent.history import budget_history, serialize_history


def msgs(*texts: str) -> list[Message]:
    """Alternating user/assistant turns, ending on a user turn."""
    roles = ["user", "assistant"]
    return [Message(roles[i % 2], text) for i, text in enumerate(texts)]


# --- budget_history -----------------------------------------------------


def test_budget_history_keeps_only_the_last_max_messages() -> None:
    history = msgs(*[f"turn {i}" for i in range(30)])

    kept = budget_history(history, max_messages=5, max_chars=100_000)

    assert kept == history[-5:]


def test_budget_history_drops_oldest_first_under_the_char_cap() -> None:
    history = [
        Message("user", "a" * 10),
        Message("assistant", "b" * 10),
        Message("user", "c" * 10),
        Message("assistant", "d" * 10),
        Message("user", "e" * 10),  # current turn
    ]

    kept = budget_history(history, max_messages=100, max_chars=25)

    # Dropped from the oldest end until the remaining total fits: e(10) + d(10)
    # = 20 <= 25, adding c would make 30 > 25, so c/b/a are all gone.
    assert kept == history[-2:]


def test_budget_history_never_drops_the_current_user_turn_even_over_budget() -> None:
    history = [
        Message("user", "short"),
        Message("assistant", "also short"),
        Message("user", "x" * 1000),  # alone busts any reasonable char cap
    ]

    kept = budget_history(history, max_messages=100, max_chars=50)

    assert kept == [history[-1]]


def test_budget_history_never_truncates_the_current_user_turn() -> None:
    current_text = "y" * 1000
    history = [Message("user", current_text)]

    kept = budget_history(history, max_messages=100, max_chars=10)

    assert kept == [Message("user", current_text)]
    assert kept[0].text == current_text, "the text itself must be untouched, not clipped"


def test_budget_history_empty_input_returns_empty() -> None:
    assert budget_history([]) == []


# --- serialize_history ----------------------------------------------------


def test_serialize_history_of_one_message_is_verbatim() -> None:
    """THE compatibility guarantee: every existing call site builds exactly one
    user message, so this branch must return that message's text unchanged —
    no wrapping, no delimiters, no prefix. This is what makes the whole
    existing test suite (and every first conversation turn) a no-op."""
    text = "what did I write about Dijkstra?"

    assert serialize_history([Message("user", text)]) == text


def test_serialize_history_multi_message_contains_prior_and_current_turns() -> None:
    history = [
        Message("user", "what did I write about Dijkstra?"),
        Message("assistant", "You covered it in algorithms.md"),
        Message("user", "and what about A*?"),
    ]

    rendered = serialize_history(history)

    assert "what did I write about Dijkstra?" in rendered
    assert "You covered it in algorithms.md" in rendered
    assert "and what about A*?" in rendered
    # The current turn must be identifiable as the live question, not buried
    # inside the delimited prior-turns block.
    assert rendered.rstrip().endswith("and what about A*?")
