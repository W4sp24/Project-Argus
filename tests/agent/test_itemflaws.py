"""Tests for the mechanical item-flaw validator.

Every BAD_* fixture here is copied verbatim out of
`15-Courses/CS26110/study/exam-2026-09-14-10q.md` in the author's vault -- a
real generated exam, and the one that prompted this work. Nine of its ten
questions test whether the reader can navigate a document they will not have
in front of them, which is what "an AI pretending to ask" looks like in
practice.

The rules themselves follow the NBME item-writing guide (one-best-answer
format, the cover-the-options rule, distractors drawn from the same category
as the key, and the published list of technical flaws) and, for cards,
SuperMemo's twenty rules -- chiefly the minimum information principle.
"""

import pytest

from backend.agent.itemflaws import (
    Item,
    check_card,
    check_item,
    check_set,
    codes,
    item_writing_rules,
)

# --- verbatim from the author's vault --------------------------------------

BAD_ACCORDING_TO = Item(
    stem=(
        "According to the excerpt, numerical analysis is the study of algorithms "
        "that use numerical approximation for problems in what area?"
    ),
    options=("mathematical analysis", "symbolic logic", "discrete geometry", "probability theory"),
    answer="mathematical analysis",
)

BAD_IS_LISTED = Item(
    stem="True or False: The excerpt lists Continuity as a slide topic.",
    answer="True",
)

BAD_SLIDE_NUMBER = Item(
    stem="What is the title of the theorem on slide 20 that includes the word Remainder?",
    answer="Taylor's Formula with (integral) Remainder",
)

BAD_CLANG_CUE = Item(
    stem="Which theorem in the excerpt is explicitly associated with continuous functions?",
    options=(
        "Intermediate - Value Theorem for continuous functions",
        "Rolle's Theorem",
        "Mean Value Theorem for Derivatives",
        "Alternating Series Theorem",
    ),
    answer="Intermediate - Value Theorem for continuous functions",
)

# --- what the replacement should look like ---------------------------------

GOOD_VIGNETTE = Item(
    stem=(
        "A function $f$ is continuous on $[0,2]$, differentiable on $(0,2)$, and "
        "$f(0)=f(2)=3$. A student concludes that $f'(c)=0$ for some $c$ in "
        "$(0,2)$. Which result justifies the conclusion?"
    ),
    options=(
        "Rolle's Theorem",
        "The Mean Value Theorem",
        "The Intermediate Value Theorem",
        "The Extreme Value Theorem",
    ),
    answer="Rolle's Theorem",
    explanation="All three of Rolle's hypotheses hold, and its conclusion is exactly $f'(c)=0$.",
)


# --- the headline flaw ------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [BAD_ACCORDING_TO, BAD_IS_LISTED, BAD_SLIDE_NUMBER, BAD_CLANG_CUE],
    ids=["according-to", "is-listed", "slide-number", "in-the-excerpt"],
)
def test_a_question_about_the_document_is_rejected(item: Item) -> None:
    """The failure that prompted all of this.

    A question about where something appears tests document navigation. The
    reader will not have the document; the citation records where it came
    from. Ask about the subject.
    """
    assert "document-reference" in codes(check_item(item))


def test_a_question_about_the_subject_is_not_rejected() -> None:
    assert check_item(GOOD_VIGNETTE) == []


# --- technical flaws --------------------------------------------------------


def test_all_of_the_above_is_rejected() -> None:
    item = Item(
        stem="Which conditions does Rolle's Theorem require?",
        options=("Continuity on the closed interval", "Differentiability inside it",
                 "Equal endpoint values", "All of the above"),
        answer="All of the above",
    )
    assert "all-of-the-above" in codes(check_item(item))


def test_a_key_much_longer_than_its_distractors_is_flagged() -> None:
    """Length is the most reliable tell a test-wise student reads."""
    item = Item(
        stem="What does the Mean Value Theorem guarantee?",
        options=(
            "That there exists an interior point at which the instantaneous rate of "
            "change equals the average rate of change across the whole interval",
            "A maximum",
            "A minimum",
            "A root",
        ),
        answer=(
            "That there exists an interior point at which the instantaneous rate of "
            "change equals the average rate of change across the whole interval"
        ),
    )
    assert "length-tell" in codes(check_item(item))


def test_an_absolute_term_in_a_distractor_is_flagged() -> None:
    """"Always" and "never" are almost always wrong, and students know it."""
    item = Item(
        stem="What does continuity on a closed interval imply?",
        options=("A bounded function", "Always a derivative", "Never a maximum", "A root"),
        answer="A bounded function",
    )
    assert "absolute-term" in codes(check_item(item))


def test_a_key_word_repeated_from_the_stem_is_a_clang_cue() -> None:
    assert "clang-cue" in codes(check_item(BAD_CLANG_CUE))


def test_duplicate_options_are_rejected() -> None:
    item = Item(
        stem="Which theorem needs equal endpoint values?",
        options=("Rolle's Theorem", "rolle's theorem", "The MVT", "The IVT"),
        answer="Rolle's Theorem",
    )
    assert "duplicate-option" in codes(check_item(item))


def test_a_key_that_is_not_among_the_options_is_rejected() -> None:
    """A question nothing can answer correctly is worse than no question."""
    item = Item(
        stem="Which theorem needs equal endpoint values?",
        options=("The MVT", "The IVT", "The EVT", "The FTC"),
        answer="Rolle's Theorem",
    )
    assert "answer-not-in-options" in codes(check_item(item))


def test_too_few_options_is_rejected() -> None:
    item = Item(
        stem="Which theorem needs equal endpoint values?",
        options=("Rolle's Theorem", "The MVT"),
        answer="Rolle's Theorem",
    )
    assert "too-few-options" in codes(check_item(item))


def test_an_unflagged_negative_stem_is_flagged() -> None:
    item = Item(
        stem="Which of the following is not a hypothesis of Rolle's Theorem?",
        options=("Continuity", "Differentiability", "Equal endpoints", "Monotonicity"),
        answer="Monotonicity",
    )
    assert "negative-stem" in codes(check_item(item))


def test_a_short_answer_question_is_not_judged_on_its_options() -> None:
    """A `short` item has none, and absent is not a flaw."""
    item = Item(stem="State the conclusion of Rolle's Theorem.", answer="$f'(c)=0$")
    assert "too-few-options" not in codes(check_item(item))


# --- across a whole paper ---------------------------------------------------


def test_a_key_position_used_too_often_is_flagged() -> None:
    """Six keys in position A is a paper a guesser scores well on."""
    items = [
        Item(
            stem=f"Question {i} about the material?",
            options=("right", "wrong one", "wrong two", "wrong three"),
            answer="right",
        )
        for i in range(6)
    ]
    assert any("unbalanced-key" in codes(flaws) for flaws in check_set(items).values())


def test_a_balanced_paper_is_not_flagged() -> None:
    options = ("alpha", "beta", "gamma", "delta")
    items = [
        Item(stem=f"Question {i} about the material?", options=options, answer=options[i % 4])
        for i in range(8)
    ]
    assert all("unbalanced-key" not in codes(flaws) for flaws in check_set(items).values())


# --- cards ------------------------------------------------------------------


def test_a_card_answer_that_is_a_paragraph_breaks_minimum_information() -> None:
    """SuperMemo's rule 4: one card, one testable thing, answer as short as possible."""
    card = Item(
        stem="What is the Mean Value Theorem?",
        answer=(
            "It states that for a function continuous on a closed interval and "
            "differentiable on the open interval there exists at least one interior "
            "point where the instantaneous rate of change is exactly equal to the "
            "average rate of change over the whole interval, which generalises "
            "Rolle's Theorem and underpins Taylor's Theorem as well."
        ),
    )
    assert "minimum-information" in codes(check_card(card))


def test_a_card_answer_that_is_a_list_is_flagged() -> None:
    """Rule 10: enumerations are the hardest thing to hold in memory."""
    card = Item(
        stem="What are the hypotheses of Rolle's Theorem?",
        answer="Continuity on [a,b]; differentiability on (a,b); f(a) equals f(b)",
    )
    assert "enumeration" in codes(check_card(card))


def test_a_card_testing_two_things_is_flagged() -> None:
    """Rule 4 again: a card testing two things tests neither."""
    card = Item(
        stem="What is a limit, and what is continuity?",
        answer="A value approached; equality of limit and value",
    )
    assert "two-facts" in codes(check_card(card))


def test_a_yes_no_card_is_flagged() -> None:
    """Recognition, not retrieval -- and a 50% score from knowing nothing."""
    card = Item(stem="Is the Mean Value Theorem important?", answer="Yes")
    assert "yes-no" in codes(check_card(card))


def test_a_good_card_passes() -> None:
    card = Item(
        stem="What does Rolle's Theorem conclude?",
        answer="There is an interior point where $f'(c)=0$.",
    )
    assert check_card(card) == []


def test_a_card_is_judged_on_the_document_rule_too() -> None:
    card = Item(stem="Which theorem does slide 14 state?", answer="Rolle's Theorem")
    assert "document-reference" in codes(check_card(card))


# --- the prompt side --------------------------------------------------------


def test_the_rules_are_stated_to_the_model_as_well_as_checked() -> None:
    """A validator alone means regenerating constantly; say the rules first."""
    rules = item_writing_rules()
    assert "cover" in rules.lower()
    assert "excerpt" in rules.lower(), "the document-reference ban must be stated"
