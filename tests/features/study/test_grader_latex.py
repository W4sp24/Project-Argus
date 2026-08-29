r"""Grading an answer the model typeset against one a student typed by hand.

``_is_correct`` compares two strings from very different places: the expected
answer was written by a model that has just been asked to use mathematical
notation, and the given answer was typed into a bare ``<input>`` by somebody
who has not. Nobody types ``\frac{1}{2}``.

``_normalize`` reduces text to ``[a-z0-9]`` and nothing else, so a backslash
command loses its backslash and *becomes a word*: ``$\frac{1}{2}$`` normalises
to ``"frac 1 2"``. Short answers are then graded by containment either way, so
that stray word is not merely noise -- it is an accepted answer. A student who
types ``frac`` scores the point.

Stripping the markup first leaves the content the comparison was always meant
to be about. The exam contract also tells the generator to keep short answers
plain, so this is the belt rather than the braces; a prompt is a request, not
a guarantee.
"""

from __future__ import annotations

import pytest

from backend.features.study.grader import _is_correct
from backend.features.study.practice_exam import Citation, Question

B = chr(92)


def _question(answer: str, **kwargs: object) -> Question:
    return Question(
        q="What is it?",
        type=kwargs.pop("type", "short"),  # type: ignore[arg-type]
        answer=answer,
        explanation="",
        citation=Citation(path="15-Courses/MATH210/materials/deck.pdf", page=1, quote="x"),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("expected", "typed"),
    [
        ("$" + B + "frac{1}{2}$", "frac"),
        ("$" + B + "dfrac{1}{2}$", "dfrac"),
        ("$" + B + "text{two}$", "text"),
        ("$" + B + "begin{cases}1{" + B + "end{cases}$", "cases"),
        ("$" + B + "left(x+1" + B + "right)$", "left"),
        ("$" + B + "mathrm{d}x$", "mathrm"),
    ],
)
def test_a_layout_command_is_not_an_acceptable_answer(expected: str, typed: str) -> None:
    """The regression this file exists for: markup graded as content."""
    assert not _is_correct(_question(expected), typed), (
        f"typing {typed!r} scored a point against {expected!r}"
    )


@pytest.mark.parametrize(
    ("expected", "typed"),
    [
        ("$" + B + "frac{1}{2}$", "1/2"),
        ("$" + B + "dfrac{1}{2}$", "1 / 2"),
        ("$" + B + "text{two}$", "two"),
        ("$" + B + "left(x+1" + B + "right)$", "(x + 1)"),
        ("$x^2$", "x^2"),
        ("$" + B + "sqrt{2}$", "sqrt(2)"),
        ("$O(" + B + "log n)$", "O(log n)"),
        ("$" + B + "alpha$", "alpha"),
    ],
)
def test_a_typeset_expected_answer_still_accepts_what_a_student_types(
    expected: str, typed: str
) -> None:
    assert _is_correct(_question(expected), typed), f"{expected!r} should accept {typed!r}"


def test_an_operator_name_is_kept_because_a_student_types_it() -> None:
    r"""``\log`` carries a word a student writes; ``\frac`` does not.

    Deleting every command outright would reduce ``$O(\log n)$`` to ``"o n"``
    and then accept a bare ``O(n)`` -- a different complexity class. So the
    strip list is layout only, and an operator keeps its name.
    """
    assert not _is_correct(_question("$O(" + B + "log n)$"), "O(n)")
    assert _is_correct(_question("$O(" + B + "log n)$"), "log n")


def test_a_wrong_answer_is_still_wrong() -> None:
    assert not _is_correct(_question("$" + B + "frac{1}{2}$"), "3/4")


def test_plain_answers_are_unaffected() -> None:
    """The overwhelmingly common case must not change behaviour at all."""
    assert _is_correct(_question("Mid-ocean ridges"), "mid ocean ridges")
    assert not _is_correct(_question("Mid-ocean ridges"), "subduction zones")


def test_an_empty_answer_is_never_correct() -> None:
    assert not _is_correct(_question("$" + B + "frac{1}{2}$"), "   ")
    assert not _is_correct(_question("$" + B + "frac{1}{2}$"), "$$")


def test_mcq_letters_still_resolve_against_a_typeset_option() -> None:
    """The letter shortcut has to survive options carrying notation."""
    question = _question(
        "$" + B + "frac{1}{2}$",
        type="mcq",
        options=["$" + B + "frac{1}{2}$", "$" + B + "frac{1}{3}$", "0", "1"],
    )

    assert _is_correct(question, "a")
    assert not _is_correct(question, "b")


def test_two_options_that_differ_only_in_notation_stay_distinct() -> None:
    r"""Stripping must not collapse ``\frac{1}{2}`` and ``\frac{1}{3}`` together."""
    question = _question(
        "$" + B + "frac{1}{2}$",
        type="mcq",
        options=["$" + B + "frac{1}{2}$", "$" + B + "frac{1}{3}$"],
    )

    assert _is_correct(question, "$" + B + "frac{1}{2}$")
    assert not _is_correct(question, "$" + B + "frac{1}{3}$")
