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
from typing import Any

_PROMPTS = Path(__file__).parent / "prompts"

#: Notation and markdown. Handed to every path that asks a model for prose.
MATH_PROMPT = _PROMPTS / "formatting.md"

#: What separates a note worth revising from a summary. Notes and study guides
#: only -- a chat answer is a conversation, not a study aid, and an exam is
#: neither.
NOTE_QUALITY_PROMPT = _PROMPTS / "note_quality.md"

#: Notation for a model answering in JSON rather than in markdown.
JSON_MATH_PROMPT = _PROMPTS / "formatting_json.md"

#: The trailing ``## Topics`` section that becomes a note's concept links.
#: Notes and study guides only, for the same reason NOTE_QUALITY_PROMPT is --
#: a chat answer has nowhere to put links, and an exam is JSON.
TOPICS_PROMPT = _PROMPTS / "topics.md"

#: The ``Q::``/``A::`` self-test. Its own file because it is a *section*,
#: and which sections a note has is the doc type's decision, not a rule
#: that applies to every note whatever its shape.
SELF_TEST_PROMPT = _PROMPTS / "self_test.md"


@cache
def math_contract() -> str:
    """How to write notation so it renders in Argus *and* in Obsidian."""
    return MATH_PROMPT.read_text(encoding="utf-8").strip()


@cache
def note_quality() -> str:
    """What a note has to do to be worth coming back to."""
    return NOTE_QUALITY_PROMPT.read_text(encoding="utf-8").strip()


@cache
def topics_tail() -> str:
    """Ask for the concept names a note's links are built from.

    Shared by the four per-document note styles and the course-wide study
    guide, for the same reason :func:`note_quality` is: two copies of "name
    the concepts" is how a guide and the note beside it end up connected to
    the vault by different rules.

    The whole feature is designed so that a model ignoring this section costs
    nothing. :func:`backend.vault.relations.parse_topics` finds no section,
    returns no topics, and the note is written exactly as it was before this
    existed -- only the concept links are missing. There is no retry, no
    fallback prompt and no error stage behind this, and that is deliberate.
    """
    return TOPICS_PROMPT.read_text(encoding="utf-8").strip()


@cache
def json_math_contract() -> str:
    r"""Notation rules for a reply that is JSON rather than markdown.

    A narrowing of :func:`math_contract`, not an addition to it, and the two
    are mutually exclusive at any one call site. Three of the markdown rules
    invert once the answer is a JSON document:

    * A backslash has to be doubled, because JSON has already claimed it.
    * ``$$`` display blocks are out -- every string here is rendered inside a
      line of a question, not as a block of its own.
    * The ``answer`` field of a short question carries no notation at all. It
      is string-compared against what a person typed into an ``<input>``, and
      no one types ``\frac{1}{2}``.

    Handing the markdown contract to the exam generator would therefore
    actively instruct it to produce something the exam cannot use.
    """
    return JSON_MATH_PROMPT.read_text(encoding="utf-8").strip()


@cache
def self_test_tail() -> str:
    """Ask for the ``Q::``/``A::`` pairs a note's flashcards are parsed from.

    Split out of :func:`note_quality` because it is a *section*, and sections
    are owned by the doc type. Left inside the quality rules it applied to
    every note whatever its shape, so an FAQ was asked for its questions twice
    and Cornell notes got a self-test under their own Cues.
    """
    return SELF_TEST_PROMPT.read_text(encoding="utf-8").strip()


def note_contract(doc_type: Any = None) -> str:
    """The whole contract for one generated note, in the one correct order.

    This function exists because section order is load-bearing and was
    previously decided by three prompt files that disagreed.
    :func:`backend.vault.relations.parse_topics` keeps everything *before* the
    last ``## Topics`` heading and discards the rest -- so a note that took
    "end every note with a self-test" literally had its self-test deleted on
    the way into the vault, along with the flashcards it would have become.

    Order, therefore, is stated once, here:

    1. the doc type's own sections
    2. how to write a note worth keeping
    3. how to write notation that renders in both engines
    4. the self-test, if this type has one
    5. ``## Topics``, always last, because everything after it is thrown away

    ``doc_type`` of ``None`` means "no prescribed sections" -- a note written
    from a free-text instruction alone. It still gets everything from (2)
    down, because those are house rules rather than properties of a shape:
    a note asked for in the user's own words is still a note, and still has
    to render in Obsidian and still seeds a deck. Defaulting to a shape
    instead would silently impose sections nobody asked for.
    """
    from backend.agent.doctypes import contract

    sections = contract(doc_type.key) if doc_type else ""
    wants_self_test = doc_type.wants_self_test if doc_type else True
    wants_topics = doc_type.wants_topics if doc_type else True
    return compose(
        sections,
        note_quality(),
        math_contract(),
        self_test_tail() if wants_self_test else "",
        topics_tail() if wants_topics else "",
    )


def compose(*blocks: str) -> str:
    """Join prompt blocks, dropping the empty ones.

    Callers assemble a prompt out of some fixed contract blocks and some
    caller-supplied ones that may be absent -- a note style with no free-text
    instruction, say. Filtering here keeps the blank-line arithmetic out of
    every call site, and keeps a missing block from leaving a hole in the
    prompt that reads to the model like a section it failed to fill in.
    """
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())
