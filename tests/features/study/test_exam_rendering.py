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


# --- the paper reads like a paper -------------------------------------------


def _exam_with(**overrides) -> Exam:
    question = Question(
        q="A function $f$ is continuous on $[0,2]$. Which result applies?",
        type="mcq",
        options=["Rolle's Theorem", "The MVT", "The IVT", "The EVT"],
        answer="Rolle's Theorem",
        explanation="All three hypotheses hold.",
        citation=Citation(path="wk9.pdf", slide=14, quote="Rolle's Theorem"),
    )
    return Exam(course="CS26110", title="Preliminaries", questions=[question], **overrides)


def test_an_exam_carries_frontmatter() -> None:
    """A bare `# H1` is invisible to relink, to the WRITTEN badge and to Obsidian."""
    body = render_exam_md(_exam_with(), course="CS26110", rel_path="15-Courses/CS26110/study/e.md")

    assert body.startswith("---")
    assert "generated_by: argus" in body
    assert "course: CS26110" in body
    assert "type: exam" in body


def test_the_key_links_back_to_its_paper_and_the_paper_to_its_key() -> None:
    """It used to say "the matching `-key.md` file" as literal, unclickable text."""
    rel = "15-Courses/CS26110/study/exam-2026-09-14-10q.md"
    paper = render_exam_md(_exam_with(), course="CS26110", rel_path=rel)
    key = render_key_md(_exam_with(), course="CS26110", rel_path=rel)

    assert "[[exam-2026-09-14-10q-key|answer key]]" in paper
    assert "[[exam-2026-09-14-10q|the paper]]" in key


def test_the_paper_says_how_long_it_should_take() -> None:
    assert "minutes" in render_exam_md(_exam_with())


def test_a_short_question_gets_somewhere_to_write() -> None:
    """It used to render as a heading and then nothing at all."""
    exam = Exam(
        course="CS26110",
        title="T",
        questions=[
            Question(
                q="State the conclusion of Rolle's Theorem.",
                type="short",
                answer="$f'(c)=0$",
                citation=Citation(path="wk9.pdf", slide=14, quote="Rolle"),
            )
        ],
    )
    assert "_Answer:_" in render_exam_md(exam)


def test_balanced_inline_maths_is_left_alone() -> None:
    """The contract asks for LaTeX in `q`; escaping it would print the dollars."""
    body = render_exam_md(_exam_with())
    assert "$f$" in body and B + "$f" not in body


def test_an_unbalanced_dollar_is_neutralised() -> None:
    """One stray `$` opens a span that swallows the next several questions."""
    exam = Exam(
        course="CS26110",
        title="T",
        questions=[
            Question(
                q="A licence costs $200 per seat. What is the total for 3 seats?",
                type="short",
                answer="600",
                citation=Citation(path="wk1.pdf", page=2, quote="licence"),
            )
        ],
    )
    assert B + "$200" in render_exam_md(exam)
