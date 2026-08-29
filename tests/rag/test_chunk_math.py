r"""Chunking a note that contains mathematics.

``_atomic_units`` already refuses to split a fenced code block, for the
obvious reason: half a code block retrieved on its own is worse than useless.
A display equation is the same shape of problem and had none of the same
protection -- ``$$ ... $$`` spanning several lines was windowed like ordinary
prose, so half an equation could be indexed as a chunk and the other half
turned up in the next one.

That mattered little while nothing in the vault was written in LaTeX. It
matters now that every generated note is.

The neighbouring bug this file also pins: ``_sections`` runs the heading regex
over raw text with no fence awareness at all, so a ``#`` comment inside a
Python block has always started a new section.
"""

from __future__ import annotations

from backend.rag.chunk import chunk_blocks
from backend.rag.extract import Block

FILLER = "\n".join(f"line {i} with some padding words to grow the section" for i in range(80))


def _chunks(text: str):
    return chunk_blocks([Block(text=text, meta={"frontmatter": {}})], "note.md")


def test_a_display_equation_is_never_split_across_chunks() -> None:
    equation = "$$\n" + "\n".join(f"x_{{{i}}} = {i} + y \\\\" for i in range(40)) + "\n$$"
    chunks = _chunks(f"# Big\n\n{FILLER}\n\n{equation}\n\n{FILLER}\n")

    assert len(chunks) >= 2, "the section should have split at all"
    for chunk in chunks:
        assert chunk.text.count("$$") % 2 == 0, (
            f"a $$ block was split across a chunk boundary: {chunk.text[:200]!r}"
        )


def test_a_short_display_equation_stays_whole_and_intact() -> None:
    chunks = _chunks("# Gradient\n\nThe minimum is where\n\n$$\n\\nabla f(x) = 0\n$$\n\nholds.\n")

    body = "\n".join(chunk.text for chunk in chunks)
    assert "$$\n\\nabla f(x) = 0\n$$" in body


def test_a_one_line_display_equation_does_not_swallow_the_rest_of_the_note() -> None:
    r"""The case the fence rule does not have.

    ``$$E = mc^2$$`` opens *and* closes on one line, so treating any line
    starting with ``$$`` as an opener makes the scanner run to the *next*
    ``$$`` somewhere further down -- consuming every heading and paragraph in
    between into one indivisible unit.
    """
    text = (
        "# Physics\n\n"
        "$$E = mc^2$$\n\n"
        "## Later section\n\n"
        "Something else entirely.\n\n"
        "$$F = ma$$\n\n"
        "## Final section\n\n"
        "The end.\n"
    )
    chunks = _chunks(text)

    headings = {chunk.meta["heading"] for chunk in chunks}
    assert {"Physics", "Later section", "Final section"} <= headings


def test_inline_maths_is_left_entirely_alone() -> None:
    """A single ``$`` is not a delimiter for chunking purposes."""
    chunks = _chunks("# Costs\n\nIt costs $100, and $x$ is the rate.\n")

    assert "It costs $100, and $x$ is the rate." in chunks[0].text


def test_a_heading_inside_a_code_fence_does_not_start_a_section() -> None:
    """``_sections`` ran the heading regex over raw text, fences included."""
    text = (
        "# Real heading\n\n"
        "```python\n"
        "# not a heading, just a comment\n"
        "def f():\n"
        "    return 1\n"
        "```\n\n"
        "Body text after the block.\n"
    )
    chunks = _chunks(text)

    headings = {chunk.meta["heading"] for chunk in chunks}
    assert headings == {"Real heading"}, f"a comment became a section: {headings}"


def test_a_fence_and_an_equation_in_the_same_note_both_survive() -> None:
    text = (
        "# Mixed\n\n"
        "$$\n\\sum_{i=1}^n i = \\frac{n(n+1)}{2}\n$$\n\n"
        "```python\ndef total(n):\n    return n * (n + 1) // 2\n```\n"
    )
    body = "\n".join(chunk.text for chunk in _chunks(text))

    assert "\\sum_{i=1}^n i = \\frac{n(n+1)}{2}" in body
    assert "def total(n):" in body


def test_an_unterminated_display_block_does_not_lose_the_rest_of_the_note() -> None:
    """A truncated PDF extract can end mid-equation; index what is there."""
    body = "\n".join(chunk.text for chunk in _chunks("# Cut\n\nBefore.\n\n$$\nx = 1\n"))

    assert "Before." in body
    assert "x = 1" in body
