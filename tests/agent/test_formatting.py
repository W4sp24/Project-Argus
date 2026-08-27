"""One house style, reaching every path that asks a model for prose.

The point of :mod:`backend.agent.formatting` is not that the rules exist -- any
one prompt could have carried them. It is that there is exactly one copy. Four
prompt builders each growing their own formatting sentence is how a study guide
and the note filed beside it end up written to different rules, and how a
notation change lands in three places out of four.

So these tests assert *sameness* rather than content: the identical string
reaches the chat agent, the per-document note and the course-wide guide. A test
that checked each prompt "mentions LaTeX" would pass just as happily against
three divergent copies.
"""

from __future__ import annotations

from backend.agent.formatting import compose, math_contract, note_quality
from backend.agent.runtime import _load_system_prompt
from backend.core.taxonomy import Taxonomy
from backend.features.ingest import notes
from backend.features.study.study_guide import guide_prompt

CORPUS = [
    {
        "text": "Gradient descent steps against the gradient.",
        "meta": {"path": "15-Courses/CS201/materials/deck.pdf", "page": 2},
    }
]


def test_the_chat_agent_and_a_note_are_handed_the_same_contract() -> None:
    """The one assertion this module exists to make."""
    contract = math_contract()

    chat = _load_system_prompt(Taxonomy())
    note = notes.build_prompt(notes.NOTE_STYLES["summary"], "", "a/b.pdf", "TEXT")
    guide = guide_prompt("CS201", "everything", CORPUS)

    assert contract in chat
    assert contract in note
    assert contract in guide


def test_the_chat_prompt_has_no_placeholder_left_in_it() -> None:
    """A missed substitution ships the literal braces to the model.

    ``chat.md`` is templated with plain ``str.replace``, so a renamed or
    misspelt placeholder fails silently -- the prompt still loads, and the
    model is simply told ``{{FORMATTING}}``.
    """
    rendered = _load_system_prompt(Taxonomy())

    assert "{{" not in rendered
    assert "}}" not in rendered


def test_every_note_style_carries_both_contracts() -> None:
    """Notes get the note-quality half as well; a chat answer does not."""
    for key in notes.NOTE_STYLES:
        prompt = notes.build_prompt(notes.NOTE_STYLES[key], "", "a/b.pdf", "TEXT")
        assert math_contract() in prompt, f"{key} lost the notation contract"
        assert note_quality() in prompt, f"{key} lost the note-quality contract"


def test_a_note_with_no_style_still_carries_them() -> None:
    """Free-text instruction only -- the house rules are not a property of the style."""
    prompt = notes.build_prompt(None, "just summarise it", "a/b.pdf", "TEXT")

    assert math_contract() in prompt
    assert note_quality() in prompt
    assert "just summarise it" in prompt


def test_the_contract_appears_exactly_once_when_a_style_and_an_instruction_combine() -> None:
    """Composition must not stack the block per contributing part."""
    prompt = notes.build_prompt(
        notes.NOTE_STYLES["cornell"], "focus on chapter 3", "a/b.pdf", "TEXT"
    )

    assert prompt.count(math_contract()) == 1
    assert prompt.count(note_quality()) == 1


def test_a_chat_answer_is_not_held_to_the_note_rules() -> None:
    """Chat is a conversation. Ending every reply with a `Q::`/`A::` block is
    the wrong shape for one, so the note-quality half stops at the notes."""
    assert note_quality() not in _load_system_prompt(Taxonomy())


def test_the_document_text_still_reaches_the_model_after_the_contracts() -> None:
    """The contracts are preamble, not a replacement for the payload."""
    prompt = notes.build_prompt(None, "", "lectures/week3.pdf", "THE DOCUMENT BODY")

    assert "THE DOCUMENT BODY" in prompt
    assert "lectures/week3.pdf" in prompt


def test_compose_drops_empty_blocks_without_leaving_a_hole() -> None:
    """A missing optional block must not read as a section left unfilled."""
    assert compose("a", "", "b") == "a\n\nb"
    assert compose("", "  \n ", "only") == "only"
    assert compose() == ""
    assert compose("  padded  ") == "padded"
