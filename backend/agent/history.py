"""Conversation-history policy: how much history rides along, and how it is
rendered for a single-prompt backend.

Deliberately not in :mod:`backend.agent.adapters` — that module is the wire
protocol (:class:`~backend.agent.adapters.Message`, the adapter Protocol) and
is already ~600 lines; budgeting and serialization are policy layered on top
of it, and keeping them here is what lets that policy change without
touching every adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from backend.agent.adapters import Message

# Chosen to bound both prompt cost and provider context limits without
# mattering for the common case: most Argus conversations are a handful of
# turns, so these caps only bite on genuinely long sessions.
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 24_000


def budget_history(
    messages: Sequence[Message],
    *,
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[Message]:
    """Trim history to the last ``max_messages``, then to ``max_chars`` total.

    Trimming drops from the *oldest* end on both passes, because the most
    recent turns are what makes a follow-up question answerable at all.

    ``messages[-1]`` — the live user turn — is never dropped or truncated,
    even if it alone exceeds ``max_chars``: truncating the user's own
    question produces a worse answer than an over-long prompt, and a
    genuinely oversized turn fails legibly at the provider instead of being
    silently mangled here. In that case the return value is ``[messages[-1]]``
    whole, over budget but intact.
    """
    if not messages:
        return []

    current = messages[-1]
    kept = list(messages[-max_messages:])

    total = sum(len(m.text) for m in kept)
    while len(kept) > 1 and total > max_chars:
        dropped = kept.pop(0)
        total -= len(dropped.text)

    if len(kept) == 1 and len(current.text) > max_chars:
        return [current]
    return kept


#: Turns the summary of an earlier span of conversation into a message the
#: model reads as context. Injected rather than imported: this module is
#: policy, and keeping it model-free is what lets ``tests/agent/test_history``
#: run with no model, no vault and no database.
Summarizer = Callable[[Sequence[Message]], Awaitable[str]]

#: How the compacted prefix is introduced. A ``user`` turn because
#: :class:`Message` has only two roles and an ``assistant`` turn would read as
#: something the model itself said.
SUMMARY_PREFIX = "Earlier in this conversation:"

#: Never compact below this many real turns. Summarising two messages costs a
#: model call to save a few hundred characters, and the summary is longer than
#: what it replaces as often as not.
MIN_COMPACTABLE = 6


async def compact_history(
    messages: Sequence[Message],
    *,
    summarize: Summarizer,
    prior_summary: str = "",
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_chars: int = MAX_HISTORY_CHARS,
) -> tuple[list[Message], str]:
    """Budget history, but summarise what falls off instead of losing it.

    :func:`budget_history` drops the oldest turns and nothing else, so a long
    session silently forgets its own beginning: the transcript on screen stays
    complete while the model stops being able to see what the conversation was
    originally about. For a tutor that is the whole problem -- a student who
    explained on turn three what they find confusing gets asked again on turn
    forty.

    Returns the messages to send and the summary to persist. The summary
    covers everything dropped *so far*, including whatever a previous call
    already folded in, so it is written once per compaction rather than
    rebuilt per turn.

    A summarizer that fails returns the plain budgeted history: forgetting the
    start of a conversation is bad, and failing the turn outright is worse.
    """
    if not messages:
        return [], prior_summary

    kept = budget_history(messages, max_messages=max_messages, max_chars=max_chars)
    dropped = list(messages[: len(messages) - len(kept)])

    summary = prior_summary
    if len(dropped) >= MIN_COMPACTABLE:
        try:
            fresh = (await summarize(dropped)).strip()
        except Exception:  # noqa: BLE001 - a lost summary must not lose the turn
            fresh = ""
        if fresh:
            summary = fresh

    if not summary:
        return kept, prior_summary
    return [Message("user", f"{SUMMARY_PREFIX}\n{summary}"), *kept], summary


def serialize_history(messages: Sequence[Message]) -> str:
    """Render conversation history as the single prompt string the Claude SDK
    (and the probe) take.

    **When ``len(messages) == 1``, this returns ``messages[0].text``
    verbatim.** That single branch looks like a special case but is actually
    the compatibility guarantee this whole commit rests on: every existing
    call site builds exactly one user message, so every existing test and
    every first turn of every conversation round-trips through here
    byte-for-byte unchanged. Do not "simplify" it away.

    For more than one message, prior turns are rendered in a delimited block
    ahead of the current message, which stays unprefixed and last so a reader
    (model or human) can immediately tell what is actually being asked now:

        <conversation>
        user: ...
        assistant: ...
        </conversation>

        user: <current message>
    """
    if len(messages) == 1:
        return messages[0].text

    *history, current = messages
    lines = ["<conversation>"]
    lines.extend(f"{m.role}: {m.text}" for m in history)
    lines.append("</conversation>")
    lines.append("")
    lines.append(f"{current.role}: {current.text}")
    return "\n".join(lines)
