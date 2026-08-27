"""The house style for anything a model writes in prose.

One definition, every prompt. Before this, each generation path invented its
own formatting sentence -- ``notes._PROMPT`` said "write the note as markdown",
``study_guide.guide_prompt`` said "Structure (markdown)", ``chat.md`` said
nothing at all -- and none of them said anything about notation. Four prompts
drifting apart is how a study guide and the note beside it end up written to
different rules.

Why it rides in the *user* prompt rather than a system prompt: every
note-and-study generation goes through :func:`backend.agent.generate.agent_generate`,
which calls its adapter with ``system_prompt=""`` and a single user message.
There is no system prompt to put this in. Chat is the one path that has one,
so ``chat.md`` carries a ``{{FORMATTING}}`` placeholder instead and
:func:`backend.agent.runtime._load_system_prompt` substitutes it there --
the same idiom as ``{{PRIVATE_DIR}}`` and ``{{TODAY}}``.

The text lives in ``prompts/*.md`` beside ``chat.md`` and ``planner.md``
rather than in a Python string, because prompt text is prose that gets edited
like prose and reviewed like prose.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS = Path(__file__).parent / "prompts"

#: Notation and markdown. Handed to every path that asks a model for prose.
MATH_PROMPT = _PROMPTS / "formatting.md"

#: What separates a note worth revising from a summary. Notes and study guides
#: only -- a chat answer is a conversation, not a study aid, and an exam is
#: neither.
NOTE_QUALITY_PROMPT = _PROMPTS / "note_quality.md"


@cache
def math_contract() -> str:
    """How to write notation so it renders in Argus *and* in Obsidian."""
    return MATH_PROMPT.read_text(encoding="utf-8").strip()


@cache
def note_quality() -> str:
    """What a note has to do to be worth coming back to."""
    return NOTE_QUALITY_PROMPT.read_text(encoding="utf-8").strip()


def compose(*blocks: str) -> str:
    """Join prompt blocks, dropping the empty ones.

    Callers assemble a prompt out of some fixed contract blocks and some
    caller-supplied ones that may be absent -- a note style with no free-text
    instruction, say. Filtering here keeps the blank-line arithmetic out of
    every call site, and keeps a missing block from leaving a hole in the
    prompt that reads to the model like a section it failed to fill in.
    """
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())
