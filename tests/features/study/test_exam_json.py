r"""LaTeX survives the JSON round-trip out of an exam generator.

``exam_prompt`` asks for JSON, and JSON gives a backslash a second meaning.
A model that writes ``$\frac{1}{2}$`` into a string field -- which is what
asking for mathematical notation invites -- produces one of two outcomes,
neither of them an exam:

* ``\alpha``, ``\sum``, ``\left``, ``\cdot``, ``\int``, ``\pi`` are not valid
  JSON escapes at all, so ``json.loads`` raises and the whole exam dies.
* ``\frac``, ``\text``, ``\begin``, ``\rho`` *are* valid escapes, so they
  decode **silently** to a formfeed, a tab, a backspace and a carriage
  return. That corruption then passes ``_citation_verified`` (which
  normalises to ``[a-z0-9]``, so it cannot see a control character), reaches
  ``exams.questions_json``, and is rendered into the vault, permanently.

The parser therefore has to be tolerant *before* the schema is applied.

``chr(92)`` rather than a literal backslash throughout: these tests are about
how many backslashes reach the parser, and a reader should not have to count
escaping levels to know what is being asserted.
"""

from __future__ import annotations

import json

import pytest

from backend.features.study.practice_exam import StudyError, build_exam

B = chr(92)

CORPUS = [
    {
        "text": "The derivative of x squared is two x. Half of one is one half.",
        "meta": {"path": "15-Courses/MATH210/materials/deck.pdf", "page": 2},
    }
]

#: Written by the model with a *single* backslash -- the exact mistake the
#: repair pass exists to absorb. The first four decode silently to control
#: characters today; the rest raise.
LONE_BACKSLASH_COMMANDS = [
    "frac{1}{2}",
    "text{if }",
    "begin{cases}",
    "rho",
    "alpha",
    "sum",
    "left(",
    "cdot",
    "int_0^1",
    "pi",
    "sqrt{2}",
    "Delta",
]

CONTROL_CHARS = "\x08\x0c\r\t"


def _exam_json(explanation: str) -> str:
    """One well-formed question carrying `explanation` verbatim."""
    return (
        '{"title": "Calculus", "questions": [{'
        '"q": "What is the derivative of x squared?",'
        '"type": "short",'
        '"answer": "2x",'
        '"explanation": "' + explanation + '",'
        '"citation": {"path": "15-Courses/MATH210/materials/deck.pdf", "page": 2,'
        '"quote": "The derivative of x squared is two x"}}]}'
    )


@pytest.mark.parametrize("command", LONE_BACKSLASH_COMMANDS)
def test_a_lone_backslash_latex_command_survives_intact(command: str) -> None:
    """Neither a hard failure nor a silent control character."""
    exam, dropped = build_exam("MATH210", _exam_json(B + command), CORPUS)

    assert dropped == 0, f"{B + command} cost us the question"
    explanation = exam.questions[0].explanation
    assert B + command in explanation, f"{B + command!r} did not survive; got {explanation!r}"
    assert not any(char in explanation for char in CONTROL_CHARS), (
        "a control character means a backslash escape was decoded rather than kept"
    )


def test_correctly_escaped_latex_is_left_alone() -> None:
    """A model that does it right must not be 'repaired' into double backslashes."""
    raw = _exam_json(B + B + "frac{1}{2}")

    exam, dropped = build_exam("MATH210", raw, CORPUS)

    assert dropped == 0
    assert exam.questions[0].explanation == B + "frac{1}{2}"


def test_a_genuine_newline_escape_stays_a_newline() -> None:
    r"""``\n`` is the one escape a model plausibly means, so it keeps its meaning.

    This is the ambiguity the repair cannot resolve and does not try to: a
    lone ``\n`` is far more likely to be an intended line break than the start
    of ``\neq``. The exam contract tells the model to double its backslashes
    for exactly this case.
    """
    exam, _ = build_exam("MATH210", _exam_json("Step one." + B + "nStep two."), CORPUS)

    assert exam.questions[0].explanation == "Step one.\nStep two."


def test_latex_inside_a_fenced_block_still_parses() -> None:
    """The two tolerances compose: fence extraction, then backslash repair."""
    fenced = "```json\n" + _exam_json(B + "sum_{i=1}^n") + "\n```"

    exam, dropped = build_exam("MATH210", fenced, CORPUS)

    assert dropped == 0
    assert B + "sum_{i=1}^n" in exam.questions[0].explanation


def test_a_quote_is_still_an_escape() -> None:
    """Repair must not break the one escape the schema genuinely needs."""
    exam, _ = build_exam("MATH210", _exam_json("He said " + B + '"hi' + B + '".'), CORPUS)

    assert exam.questions[0].explanation == 'He said "hi".'


def test_genuinely_broken_json_still_raises() -> None:
    """Tolerance is for backslashes, not for a model that returned prose."""
    with pytest.raises(StudyError, match="invalid JSON"):
        build_exam("MATH210", "Sure! Here is your exam:", CORPUS)


def test_the_repair_does_not_disturb_a_clean_payload() -> None:
    """No backslashes at all: byte-identical behaviour to plain json.loads."""
    raw = json.dumps(json.loads(_exam_json("Plain prose, no notation.")))

    exam, dropped = build_exam("MATH210", raw, CORPUS)

    assert dropped == 0
    assert exam.questions[0].explanation == "Plain prose, no notation."
