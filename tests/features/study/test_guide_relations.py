"""A study guide is the same kind of artifact as the note beside it.

Guides were written as ``body + "\\n"``: no title, no ``type``, no course, no
links, no tags. A guide was therefore reachable only by full-text search while
the ingest note sitting in the next folder carried a full header. These tests
pin the guide to the same shape as that note -- and pin the two things a guide
does differently: its neighbours are the corpus it actually cited (so it needs
no index at all), and its notes-gap checklist is prose, not a relation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import frontmatter

from backend.core.taxonomy import Taxonomy
from backend.features.study import study_guide
from backend.vault import relations

CORPUS: list[dict[str, Any]] = [
    {
        "text": "chunk one",
        "meta": {"path": "15-Courses/ETHICS/materials/wk1.pdf", "title": "wk1", "page": 3},
    },
    {
        "text": "chunk two",
        "meta": {"path": "15-Courses/ETHICS/materials/wk2.pdf", "title": "wk2", "page": 1},
    },
    {
        "text": "chunk three",
        "meta": {"path": "15-Courses/ETHICS/materials/wk1.pdf", "title": "wk1", "page": 9},
    },
]

BODY = "## Outline\n\n- a topic\n\n## Topics\n\n- Determinism\n- Free Will\n"


def test_a_guide_now_carries_the_frontmatter_it_never_had() -> None:
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)
    post = frontmatter.loads(markdown)

    assert post["title"] == "ETHICS — midterm"
    assert post["type"] == "guide"
    assert post["course"] == "ETHICS"
    assert post["scope"] == "midterm"
    assert post["generated_by"] == "argus"
    assert "argus/guide" in post["tags"]
    assert "course/ETHICS" in post["tags"]
    assert "topic/free-will" in post["tags"]


def test_the_cited_materials_become_the_guides_neighbours_deduped() -> None:
    """A guide already holds the chunks it cited.

    Those are better neighbours than a fresh similarity query would produce --
    they are what the guide actually drew from rather than what merely reads
    like it -- and they need no index, which is why this function is callable
    with nothing but a list of dicts.
    """
    post = frontmatter.loads(study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS))

    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in post.content
    assert "[[15-Courses/ETHICS/materials/wk2.pdf|wk2]]" in post.content
    assert post.content.count("materials/wk1.pdf") == 1, "two chunks of one file is one neighbour"
    assert post["related"].count("[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]") == 1


def test_the_topics_section_is_lifted_off_the_guide_body_too() -> None:
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)
    post = frontmatter.loads(markdown)

    assert "## Topics" not in post.content
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "[[Determinism]]" in post.content


def test_a_guide_does_not_offer_itself_as_its_own_source() -> None:
    """``build_relations`` always emits a ``source`` link, but a guide has no
    single source file -- its sources are the materials it cited, which are
    already its neighbours. A ``**Source**`` line here would point at the guide
    itself."""
    markdown = study_guide.guide_markdown("ETHICS", "midterm", BODY, CORPUS)

    assert "**Source**" not in markdown
    assert "study/guide" not in markdown


def test_the_gap_checklist_is_prose_and_not_a_topic() -> None:
    """The ordering trap.

    The model puts ``## Topics`` last, so a checklist appended to the reply
    lands *after* it -- where ``parse_topics`` reads every ``- `` line as a
    concept name and then deletes everything from the heading down. The
    checklist has to be spliced into the prose after the topics come off, and
    it has to sit above the fenced Related region rather than below it.
    """
    markdown = study_guide.guide_markdown(
        "ETHICS",
        "midterm",
        BODY,
        CORPUS,
        gaps=["Kant's categorical imperative (wk3.pdf)"],
    )
    post = frontmatter.loads(markdown)

    assert "## What you haven't taken notes on" in post.content
    assert "- [ ] Kant's categorical imperative (wk3.pdf)" in post.content
    assert post.content.index("Kant's categorical") < post.content.index(relations.FENCE_START)
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "topic/kant-s-categorical-imperative-wk3-pdf" not in post["tags"]


def test_a_guide_with_no_corpus_and_no_topics_is_still_a_valid_guide() -> None:
    """The degradation path: no index, no topics section, no citations."""
    markdown = study_guide.guide_markdown("ETHICS", "midterm", "## Outline\n\n- a\n", [])
    post = frontmatter.loads(markdown)

    assert post["course"] == "ETHICS"
    assert "## Outline" in post.content
    assert "topics" not in post.metadata
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in post.content


def test_a_concept_argus_can_find_is_linked_by_path() -> None:
    markdown = study_guide.guide_markdown(
        "ETHICS",
        "midterm",
        BODY,
        CORPUS,
        resolve={"Determinism": "60-Knowledge/General/Determinism.md"}.get,
    )

    assert "[[60-Knowledge/General/Determinism|Determinism]]" in markdown
    assert "[[Free Will]]" in markdown


def test_the_course_link_follows_the_taxonomy_rather_than_a_literal_folder() -> None:
    """A vault whose courses live somewhere else still gets a working link."""
    markdown = study_guide.guide_markdown(
        "ETHICS", "midterm", BODY, [], taxonomy=Taxonomy(courses="Classes")
    )

    assert "[[Classes/ETHICS/course|ETHICS]]" in markdown
    assert "15-Courses" not in markdown


def test_the_prompt_asks_the_guide_for_a_topics_section_too() -> None:
    """One tail for notes and guides alike -- two copies of 'name the concepts'
    is how a guide and the note beside it end up connected by different rules."""
    prompt = study_guide.guide_prompt("ETHICS", "midterm", CORPUS)

    assert "## Topics" in prompt
    assert "concept name" in prompt


# --- through the writer -------------------------------------------------------


def _write(tmp_path: Path, reply: str, *, scope: str = "midterm") -> str:
    async def generate(prompt: str, model: str | None = None) -> str:
        return reply

    written = asyncio.run(
        study_guide.generate_study_guide(tmp_path, generate, CORPUS, "ETHICS", scope=scope)
    )
    return (tmp_path / written).read_text(encoding="utf-8")


def test_the_written_guide_is_the_rendered_one(tmp_path: Path) -> None:
    """What lands in the vault is what ``guide_markdown`` produced -- the whole
    point of the split is that the file and the unit under test agree."""
    written = _write(tmp_path, BODY)
    post = frontmatter.loads(written)

    assert post["type"] == "guide"
    assert post["course"] == "ETHICS"
    assert "## Outline" in post.content
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in post.content


def test_a_second_guide_the_same_day_still_does_not_replace_the_first(tmp_path: Path) -> None:
    """Pinned again here because the frontmatter change rewrites the write
    path, and this is the bug that already destroyed one guide once."""

    async def generate(prompt: str, model: str | None = None) -> str:
        return "## Outline\n\n- first\n"

    first = asyncio.run(
        study_guide.generate_study_guide(tmp_path, generate, CORPUS, "ETHICS", scope="midterm")
    )

    async def generate_again(prompt: str, model: str | None = None) -> str:
        return "## Outline\n\n- second\n"

    second = asyncio.run(
        study_guide.generate_study_guide(
            tmp_path, generate_again, CORPUS, "ETHICS", scope="midterm"
        )
    )

    assert first != second
    assert "- first" in (tmp_path / first).read_text(encoding="utf-8")
    assert "- second" in (tmp_path / second).read_text(encoding="utf-8")


def test_the_written_guide_keeps_its_gap_checklist_inside_the_prose(tmp_path: Path) -> None:
    """``notes_gap_list`` is the one thing ``generate_study_guide`` adds to a
    reply, and it must not end up below the fence -- or, worse, be read back as
    the model's topic list."""
    corpus: list[dict[str, Any]] = [
        {
            "text": "Kant argues the categorical imperative binds unconditionally.",
            "meta": {
                "path": "15-Courses/ETHICS/materials/wk3.pdf",
                "title": "wk3",
                "heading": "Categorical Imperative",
            },
        }
    ]

    async def generate(prompt: str, model: str | None = None) -> str:
        return BODY

    written = asyncio.run(
        study_guide.generate_study_guide(tmp_path, generate, corpus, "ETHICS", scope="midterm")
    )
    post = frontmatter.loads((tmp_path / written).read_text(encoding="utf-8"))

    assert "## What you haven't taken notes on" in post.content
    assert post.content.index("haven't taken notes") < post.content.index(relations.FENCE_START)
    assert post["topics"] == ["Determinism", "Free Will"]
