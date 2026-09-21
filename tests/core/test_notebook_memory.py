"""Tests for per-course tutor memory.

Thread history answers "what did we say ten minutes ago". This answers "what
should you not have to tell me again" — and most of it is *derived*:
`grader.py` has computed `weak_topics` on every attempt since exams existed
and written them to a markdown file nothing reads back.
"""

from pathlib import Path

import pytest

from backend.core import notebook_memory
from backend.core.db import connect, init_schema


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "memory.db")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_a_new_course_remembers_nothing(conn) -> None:
    assert notebook_memory.recall(conn, "CS26110") == []


def test_a_fact_round_trips(conn) -> None:
    notebook_memory.remember(
        conn, "CS26110", "confusion", "Taylor remainder", "unsure when to use Lagrange form"
    )
    [memory] = notebook_memory.recall(conn, "CS26110")

    assert memory.subject == "Taylor remainder"
    assert memory.detail == "unsure when to use Lagrange form"
    assert memory.confidence == 1


def test_seeing_the_same_thing_again_strengthens_it(conn) -> None:
    """A slip and a pattern are different, and a tutor should be able to tell."""
    for _ in range(3):
        notebook_memory.remember(
            conn, "CS26110", "weak-topic", "Rolle", "missed in a practice exam"
        )

    [memory] = notebook_memory.recall(conn, "CS26110")
    assert memory.confidence == 3
    assert "seen 3x" in memory.render()


def test_the_strongest_facts_come_first(conn) -> None:
    notebook_memory.remember(conn, "CS26110", "weak-topic", "once", "d")
    for _ in range(4):
        notebook_memory.remember(conn, "CS26110", "weak-topic", "often", "d")

    assert [m.subject for m in notebook_memory.recall(conn, "CS26110")] == ["often", "once"]


def test_recall_is_capped(conn) -> None:
    """A tutor reciting thirty remembered things is worse than one recalling three."""
    for index in range(20):
        notebook_memory.remember(conn, "CS26110", "weak-topic", f"topic {index}", "d")

    assert len(notebook_memory.recall(conn, "CS26110")) == notebook_memory.MAX_RECALLED


def test_courses_do_not_see_each_other(conn) -> None:
    notebook_memory.remember(conn, "CS26110", "weak-topic", "Rolle", "d")
    notebook_memory.remember(conn, "ICS26011", "weak-topic", "Futures", "d")

    assert [m.subject for m in notebook_memory.recall(conn, "CS26110")] == ["Rolle"]


def test_a_mastered_topic_can_be_forgotten(conn) -> None:
    notebook_memory.remember(conn, "CS26110", "weak-topic", "Rolle", "d")
    notebook_memory.forget(conn, "CS26110", "weak-topic", "Rolle")

    assert notebook_memory.recall(conn, "CS26110") == []


def test_deleting_a_course_forgets_it(conn) -> None:
    """Nothing in this schema cascades, so this has to be explicit."""
    notebook_memory.remember(conn, "CS26110", "weak-topic", "Rolle", "d")
    notebook_memory.remember(conn, "CS26110", "confusion", "epsilon", "d")

    assert notebook_memory.forget_course(conn, "CS26110") == 2
    assert notebook_memory.recall(conn, "CS26110") == []


def test_an_unknown_kind_is_refused(conn) -> None:
    """The value is in the kinds meaning something rather than being free text."""
    with pytest.raises(ValueError, match="unknown memory kind"):
        notebook_memory.remember(conn, "CS26110", "vibes", "x", "y")


def test_nothing_remembered_means_no_prompt_block() -> None:
    """An empty heading in front of every turn is worse than silence."""
    assert notebook_memory.as_prompt_block([]) == ""


def test_the_prompt_block_says_not_to_recite_it(conn) -> None:
    """A model told "the student is weak on X" tends to announce it."""
    notebook_memory.remember(conn, "CS26110", "weak-topic", "Rolle", "missed twice")
    block = notebook_memory.as_prompt_block(notebook_memory.recall(conn, "CS26110"))

    assert "Rolle" in block
    assert "not recite" in block
