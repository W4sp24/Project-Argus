"""What survives the trip from a generator's reply into a written study guide.

The guide path shares :mod:`backend.features.study.practice_exam` for its
prompt budget and error type, and for a long time it shared that module's
fence handling too. It should not: an exam reply *is* a fenced JSON payload,
so extracting the fence's contents is the whole job there, while a guide reply
is prose that may legitimately *contain* a fence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import frontmatter
import pytest

from backend.features.study.practice_exam import StudyError
from backend.features.study.study_guide import generate_study_guide

CORPUS: list[dict[str, Any]] = [
    {
        "text": "A binary search halves the interval each step, so it runs in log time.",
        "meta": {"path": "15-Courses/CS201/materials/deck.pdf", "page": 4, "course": "CS201"},
    }
]


def _write(tmp_path: Path, reply: str, scope: str = "binary search") -> str:
    """Run the guide generator over a canned reply; return the written body."""

    async def generate(prompt: str, model: str | None = None) -> str:
        return reply

    written = asyncio.run(generate_study_guide(tmp_path, generate, CORPUS, "CS201", scope=scope))
    return (tmp_path / written).read_text(encoding="utf-8")


def test_a_guide_containing_a_code_fence_keeps_its_prose(tmp_path: Path) -> None:
    r"""The regression this file exists for.

    ``_strip_fences`` is a JSON *extractor* -- ```` ```(?:json)?\s*(.*?)``` ````
    returns the fence's contents and throws the rest away. Applied to a guide,
    a single worked example written as a code block replaced the entire
    document with that block's body: the outline, the concepts and the
    citations all silently vanished, and what landed in the vault was three
    lines of Python.
    """
    body = _write(
        tmp_path,
        "## Outline\n\n- Binary search [15-Courses/CS201/materials/deck.pdf p.4]\n\n"
        "## Worked examples\n\n"
        "```python\n"
        "def search(xs, target):\n"
        "    return bisect_left(xs, target)\n"
        "```\n\n"
        "That halves the interval each step.\n",
    )

    assert "## Outline" in body, "the guide's own structure must survive a fenced example"
    assert "def search(xs, target):" in body, "the example itself must survive too"
    assert "That halves the interval each step." in body, "prose after the fence must survive"


def test_two_code_fences_both_survive(tmp_path: Path) -> None:
    """The non-greedy ``(.*?)`` made a second fence the end of the document."""
    body = _write(
        tmp_path,
        "Intro.\n\n```python\nfirst()\n```\n\nMiddle.\n\n```python\nsecond()\n```\n\nEnd.\n",
        scope="fences",
    )

    assert "first()" in body
    assert "second()" in body
    assert "End." in body


def test_a_reply_wrapped_entirely_in_one_fence_is_unwrapped(tmp_path: Path) -> None:
    """The behaviour worth keeping: a model that fences its whole answer.

    This is why the old call existed, and it is a real habit of smaller
    models -- so unwrapping stays, narrowed to the case where the fence *is*
    the whole reply rather than any fence anywhere in it.
    """
    body = _write(tmp_path, "```markdown\n## Outline\n\n- Binary search\n```", scope="wrapped")

    # `.content` rather than the file: a guide now opens with a frontmatter
    # block (study_guide.guide_markdown), so "the reply was unwrapped" is a
    # statement about the prose, not about byte zero of the file.
    assert frontmatter.loads(body).content.startswith("## Outline"), (
        "a whole-reply fence is still unwrapped"
    )
    assert "```" not in body


def test_an_empty_reply_is_an_error_not_an_empty_note(tmp_path: Path) -> None:
    with pytest.raises(StudyError):
        _write(tmp_path, "   \n", scope="empty")


# --- not writing over the last one --------------------------------------------


def _write_path(tmp_path: Path, reply: str, scope: str = "binary search") -> str:
    """Same as `_write`, but returns where it landed rather than what it says."""

    async def generate(prompt: str, model: str | None = None) -> str:
        return reply

    return asyncio.run(generate_study_guide(tmp_path, generate, CORPUS, "CS201", scope=scope))


def test_a_second_guide_the_same_day_does_not_replace_the_first(tmp_path: Path) -> None:
    """The name is course + scope + day by construction, so two runs collide by
    construction too -- and re-running after tweaking the source selection is
    exactly the workflow the Course Hub encourages. The first guide used to be
    silently written over; the exam path has always counted past a collision."""
    first = _write_path(tmp_path, "The first guide.")
    second = _write_path(tmp_path, "The second guide.")

    assert first != second
    # Same reason as above: the prose is what must not have been written
    # over, and it now sits below a frontmatter block.
    first_post = frontmatter.loads((tmp_path / first).read_text(encoding="utf-8"))
    second_post = frontmatter.loads((tmp_path / second).read_text(encoding="utf-8"))
    assert first_post.content.startswith("The first guide.")
    assert second_post.content.startswith("The second guide.")


def test_a_guide_is_written_inside_its_own_course(tmp_path: Path) -> None:
    """`course` reaches `tax.course_study(course)`, which is mkdir'd and written
    to. The route sanitises it now; this pins the property that matters -- the
    file lands under the course, not somewhere a name could steer it."""
    written = _write_path(tmp_path, "Contained.")

    assert written.startswith("15-Courses/CS201/")
    assert ".." not in written
    assert (tmp_path / written).resolve().is_relative_to(tmp_path.resolve())
