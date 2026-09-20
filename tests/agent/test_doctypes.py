"""Tests for the doc-type registry and the note contract it assembles.

The ordering assertions here are not style. `relations.parse_topics` keeps
`body[: heading.start()]` of the **last** `## Topics` and discards everything
after it, so a contract that let a model put its self-test below that heading
silently deleted the self-test -- and the flashcards it is parsed into -- on
the way into the vault.
"""

import pytest

from backend.agent.doctypes import (
    DOC_TYPES,
    DocType,
    DocTypeError,
    contract,
    resolve,
    suggest,
)
from backend.agent.formatting import json_math_contract, math_contract, note_contract


def test_every_type_has_a_contract_file() -> None:
    """A registry entry with no prompt file is a 500 at generation time."""
    for key in DOC_TYPES:
        assert contract(key), f"{key} has an empty contract"


def test_every_contract_names_its_sections() -> None:
    for key in DOC_TYPES:
        assert "##" in contract(key), f"{key} asks for no sections"


def test_topics_is_the_last_section() -> None:
    """Anything after the last `## Topics` is discarded by parse_topics."""
    for key, doc_type in DOC_TYPES.items():
        if not doc_type.wants_topics:
            continue
        body = note_contract(doc_type)
        assert "## Topics" in body, key
        after = body.split("## Topics", 1)[1]
        assert "## Self-test" not in after, (
            f"{key} puts the self-test after ## Topics, where parse_topics "
            "deletes it"
        )


def test_a_self_test_type_asks_for_one_exactly_once() -> None:
    body = note_contract(resolve("study-guide"))
    assert body.count("## Self-test") == 1


def test_even_a_question_shaped_type_seeds_a_deck() -> None:
    """The overlap is the lesser cost.

    An FAQ's `###` headings and Cornell's cues read like questions but neither
    parses: `parse_qa_pairs` reads `Q::` and `A::` at the start of a line and
    nothing else. A shape that skipped the self-test to avoid asking twice
    would seed no deck at all, which is a worse note to own.
    """
    for key in DOC_TYPES:
        assert "## Self-test" in note_contract(resolve(key)), key


def test_the_markdown_and_json_contracts_are_never_composed_together() -> None:
    """They are mutually exclusive: three of the markdown rules invert."""
    body = note_contract(resolve("briefing"))
    assert math_contract()[:60] in body
    assert json_math_contract()[:60] not in body


def test_legacy_style_keys_still_resolve() -> None:
    """`ingest_jobs.note_style` holds these on every historical row."""
    assert resolve("summary").key == "briefing"
    assert resolve("study-guide").key == "study-guide"
    assert resolve("cornell").key == "cornell"
    assert resolve("key-terms").key == "key-terms"


def test_an_unknown_type_is_refused_by_name() -> None:
    """Silently defaulting would hand back a note nobody asked for."""
    with pytest.raises(DocTypeError, match="unknown note type"):
        resolve("interpretive-dance")


def test_no_type_means_the_default() -> None:
    assert resolve(None).key == "study-guide"
    assert resolve("").key == "study-guide"


def test_maths_material_suggests_a_study_guide() -> None:
    text = "The limit $\\lim_{x \\to a} f(x) = L$ " * 10
    assert suggest(text) == "study-guide"


def test_a_chronology_suggests_a_timeline() -> None:
    text = (
        "In 1642 Newton was born. By 1687 the Principia appeared. "
        "In 1704 Opticks followed, and in 1727 he died. The 18th century "
        "then saw 1750 and 1799 bring further work."
    )
    assert suggest(text) == "timeline"


def test_vocabulary_material_suggests_key_terms() -> None:
    text = " ".join(f"A term{i} is defined as something." for i in range(12))
    assert suggest(text) == "key-terms"


def test_equations_beat_definitions() -> None:
    """A maths lecture is full of definitions too; it is still a study guide."""
    text = "$x$ " * 20 + " ".join(f"A term{i} is defined as x." for i in range(12))
    assert suggest(text) == "study-guide"


def test_a_doc_type_is_hashable_so_the_contract_cache_works() -> None:
    assert isinstance(DOC_TYPES["briefing"], DocType)
    assert hash(DOC_TYPES["briefing"])
