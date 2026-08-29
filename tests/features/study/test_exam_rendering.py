r"""The exam's own notation rules, and the answer key they get rendered into.

An exam is the one generated artefact whose contract is a *narrowing* of the
house style rather than a copy of it. Three markdown rules invert once the
reply is JSON and the result is graded by string comparison, so
``json_math_contract`` exists to say so and this file pins the difference.
"""

from __future__ import annotations

from backend.agent.formatting import json_math_contract, math_contract
from backend.features.study.practice_exam import (
    Citation,
    Exam,
    Question,
    exam_prompt,
    render_exam_md,
    render_key_md,
)

B = chr(92)

CORPUS = [
    {
        "text": "Gradient descent steps against the gradient of the loss.",
        "meta": {"path": "15-Courses/CS201/materials/deck.pdf", "page": 2},
    }
]


def _exam(**overrides: object) -> Exam:
    question = Question(
        q=str(overrides.get("q", "What does the learning rate control?")),
        type="short",
        answer=str(overrides.get("answer", "the step size")),
        explanation=str(overrides.get("explanation", "It scales the update.")),
        citation=Citation(
            path="15-Courses/CS201/materials/deck.pdf",
            page=2,
            quote=str(overrides.get("quote", "steps against the gradient")),
        ),
    )
    return Exam(course="CS201", title="CS201 practice exam", questions=[question])


def test_the_exam_prompt_gets_the_json_contract_not_the_markdown_one() -> None:
    """Handing over the markdown contract would ask for what this cannot use.

    It mandates ``$$`` display blocks and single backslashes -- one breaks the
    layout of a question, the other breaks the parse.
    """
    prompt = exam_prompt("CS201", CORPUS, None, 5, "medium")

    assert json_math_contract() in prompt
    assert math_contract() not in prompt


def test_the_json_contract_says_the_three_things_the_pipeline_depends_on() -> None:
    """These are load-bearing, not stylistic.

    Each corresponds to a place the exam pipeline breaks: `_decode_payload`
    for backslashes, `_is_correct` for a plain `answer`, and the single-line
    rendering in `render_exam_md` for newlines.
    """
    contract = json_math_contract()

    assert B + B in contract, "must tell the model to double its backslashes"
    assert "$$" in contract, "must rule out display blocks explicitly"
    assert "answer" in contract, "must say the answer field stays plain"


def test_the_answer_key_separates_its_fields_with_blank_lines() -> None:
    r"""Consecutive lines are one paragraph with soft breaks.

    That put ``**Why:**`` and everything after it mid-paragraph, so a display
    block in an explanation never started a line -- and a ``$$`` that does not
    start a line is not maths in either renderer, it is two dollar signs.
    """
    key = render_key_md(_exam())

    assert "\n\n**Answer:**" in key
    assert "\n\n**Why:**" in key
    assert "\n\n**Source:**" in key


def test_a_dollar_in_a_cited_quote_cannot_open_an_equation() -> None:
    """A quote is verbatim source text, so it can carry a price.

    Left bare, that ``$`` opens a maths span that runs to the next ``$``
    somewhere further down the key, swallowing the questions in between.
    """
    key = render_key_md(_exam(quote="the licence costs $100 per seat"))

    assert B + "$100" in key
    assert "costs $100" not in key


def test_an_already_escaped_dollar_is_not_escaped_twice() -> None:
    key = render_key_md(_exam(quote="costs " + B + "$100"))

    assert B + B + "$" not in key


def test_inline_maths_in_a_question_survives_into_both_renderings() -> None:
    exam = _exam(q="What is $" + B + "nabla f(x)$ at a minimum?", explanation="It is $0$.")

    assert "$" + B + "nabla f(x)$" in render_exam_md(exam)
    assert "$" + B + "nabla f(x)$" in render_key_md(exam)
    assert "It is $0$." in render_key_md(exam)
