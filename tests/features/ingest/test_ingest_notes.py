"""Where a generated note goes, and what its prompt says.

The placement rule is the part worth pinning down: a course material's note
belongs in that course's ``notes/`` zone, everything else keeps the old
beside-the-file ``.summary.md``.
"""

from __future__ import annotations

import frontmatter
import pytest

from backend.core.taxonomy import Taxonomy
from backend.features.ingest import notes
from backend.features.study.study_guide import notes_gap_list


def test_course_material_note_lands_in_the_courses_notes_zone():
    destination = notes.note_destination("15-Courses/CS201/materials/lecture-03.pdf")
    assert destination == "15-Courses/CS201/notes/lecture-03.notes.md"


def test_note_for_a_file_outside_a_course_stays_beside_it():
    assert notes.note_destination("00-Inbox/files/paper.pdf") == "00-Inbox/files/paper.summary.md"


def test_a_file_already_in_notes_is_not_treated_as_a_material():
    """Only ``materials/`` triggers the course rule -- a note about a note
    would otherwise be written into the same folder with the same stem."""
    assert notes.note_destination("15-Courses/CS201/notes/mine.md") == (
        "15-Courses/CS201/notes/mine.summary.md"
    )


def test_the_courses_zone_is_taken_from_the_taxonomy_not_hardcoded():
    tax = Taxonomy(courses="Modules")
    assert notes.course_of("Modules/CS201/materials/a.pdf", taxonomy=tax) == "CS201"
    assert notes.course_of("15-Courses/CS201/materials/a.pdf", taxonomy=tax) is None
    assert notes.note_destination("Modules/CS201/materials/a.pdf", taxonomy=tax) == (
        "Modules/CS201/notes/a.notes.md"
    )


def test_the_note_name_follows_the_deduped_source_not_the_upload():
    """``save_ingest_file`` renames a colliding upload to ``lecture-2.pdf``;
    the note must follow, or the second ingest collides with the first note
    and ``create_note`` (create-only) fails the item."""
    assert notes.note_destination("15-Courses/CS201/materials/lecture-2.pdf") == (
        "15-Courses/CS201/notes/lecture-2.notes.md"
    )


@pytest.mark.parametrize("key", ["summary", "study-guide", "cornell", "key-terms"])
def test_every_style_contributes_its_structure_to_the_prompt(key):
    style = notes.NOTE_STYLES[key]
    prompt = notes.build_prompt(style, "", "a/b.pdf", "TEXT")
    assert style.instruction in prompt
    assert "a/b.pdf" in prompt
    assert "TEXT" in prompt


def test_a_custom_instruction_appends_to_the_style_rather_than_replacing_it():
    style = notes.NOTE_STYLES["study-guide"]
    prompt = notes.build_prompt(style, "focus on chapter 4", "a/b.pdf", "TEXT")
    assert style.instruction in prompt
    assert "focus on chapter 4" in prompt


def test_a_bare_instruction_with_no_style_stands_alone():
    """The behaviour this feature had before styles existed."""
    prompt = notes.build_prompt(None, "list the definitions", "a/b.pdf", "TEXT")
    assert "list the definitions" in prompt
    for style in notes.NOTE_STYLES.values():
        assert style.instruction not in prompt


def test_the_document_text_is_capped():
    prompt = notes.build_prompt(None, "go", "a/b.md", "x" * (notes.MAX_NOTE_CHARS + 500))
    assert "x" * notes.MAX_NOTE_CHARS in prompt
    assert "x" * (notes.MAX_NOTE_CHARS + 1) not in prompt


def test_an_unknown_style_names_the_valid_ones():
    with pytest.raises(notes.NoteStyleError) as caught:
        notes.resolve_style("outline")
    assert "study-guide" in str(caught.value)


def test_no_style_resolves_to_none():
    assert notes.resolve_style("") is None
    assert notes.resolve_style(None) is None
    assert notes.resolve_style("  ") is None


def test_the_note_carries_its_source_style_and_course_in_frontmatter():
    _, markdown = notes.note_markdown(
        "15-Courses/CS201/materials/lecture-03.pdf",
        notes.NOTE_STYLES["cornell"],
        "extra",
        "## Cues\n\n- what is X?\n",
    )
    post = frontmatter.loads(markdown)
    assert post.metadata["source"] == "15-Courses/CS201/materials/lecture-03.pdf"
    assert post.metadata["note_style"] == "cornell"
    assert post.metadata["generated_by"] == notes.GENERATED_BY
    assert post.metadata["course"] == "CS201"
    assert post.metadata["prompt"] == "extra"
    # The trailing wikilink is what retrieve.py's one-hop link expansion walks
    # back to the source with -- it is not decoration.
    assert post.content.rstrip().endswith("[[lecture-03]]")


def test_a_note_outside_a_course_carries_no_course_field():
    _, markdown = notes.note_markdown("00-Inbox/files/paper.pdf", None, "", "body")
    assert "course" not in frontmatter.loads(markdown).metadata


def test_a_generated_note_does_not_count_as_your_own_notes():
    """``notes_gap_list`` answers "what haven't you taken notes on". Once
    generated notes live under ``notes/`` too, counting them would make every
    ingested lecture look already covered."""
    corpus = [
        {
            "text": "Dijkstra shortest path relaxation queue",
            "meta": {"path": "15-Courses/CS201/materials/lecture-03.pdf", "heading": "Dijkstra"},
        },
        {
            "text": "dijkstra shortest path relaxation queue",
            "meta": {"path": "15-Courses/CS201/notes/lecture-03.notes.md"},
        },
    ]
    assert notes_gap_list(corpus) != []

    handwritten = [
        corpus[0],
        {
            "text": "dijkstra shortest path relaxation queue",
            "meta": {"path": "15-Courses/CS201/notes/my-notes.md"},
        },
    ]
    assert notes_gap_list(handwritten) == []
