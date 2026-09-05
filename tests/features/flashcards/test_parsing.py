"""Both ways text becomes cards: ``Q::``/``A::`` notes, and pasted rows."""

from __future__ import annotations

import pytest

from backend.features.flashcards.parsing import parse_delimited, parse_qa_pairs


def test_qa_pairs_span_multiple_lines() -> None:
    text = "Q:: what is a monad\nreally\nA:: a monoid in the category\nof endofunctors\n"
    assert parse_qa_pairs(text) == [
        ("what is a monad\nreally", "a monoid in the category\nof endofunctors")
    ]


def test_qa_pair_missing_its_answer_is_dropped() -> None:
    assert parse_qa_pairs("Q:: lonely question\nQ:: paired\nA:: yes") == [("paired", "yes")]


def test_qa_pairs_survive_surrounding_prose() -> None:
    """The tails ingest writes sit at the end of a real note, not alone."""
    text = "# Lecture 3\n\nSome prose.\n\n## Self-test\n\nQ:: what is P\nA:: polynomial time\n"
    assert parse_qa_pairs(text) == [("what is P", "polynomial time")]


def test_delimited_tab_and_newline() -> None:
    assert parse_delimited("front\tback\nsecond\tpair", field="tab", row="newline") == [
        ("front", "back"),
        ("second", "pair"),
    ]


def test_delimited_comma_and_semicolon() -> None:
    assert parse_delimited("a,1; b,2", field="comma", row="semicolon") == [("a", "1"), ("b", "2")]


def test_delimited_splits_on_the_first_field_delimiter_only() -> None:
    # A definition legitimately contains commas. Splitting greedily would
    # silently truncate exactly the cards worth writing.
    assert parse_delimited("term,a, b, and c", field="comma", row="newline") == [
        ("term", "a, b, and c")
    ]


def test_delimited_dash_keeps_a_hyphenated_answer_whole() -> None:
    assert parse_delimited("big-O-an upper bound", field="dash", row="newline") == [
        ("big", "O-an upper bound")
    ]


def test_delimited_skips_rows_with_no_delimiter_or_an_empty_half() -> None:
    text = "good\tpair\nlonely\n\tnofront\nnoback\t"
    assert parse_delimited(text, field="tab", row="newline") == [("good", "pair")]


def test_delimited_tolerates_crlf() -> None:
    assert parse_delimited("a\tb\r\nc\td", field="tab", row="newline") == [("a", "b"), ("c", "d")]


def test_delimited_ignores_blank_rows() -> None:
    assert parse_delimited("a\tb\n\n\nc\td\n", field="tab", row="newline") == [
        ("a", "b"),
        ("c", "d"),
    ]


def test_delimited_rejects_an_unknown_delimiter_name() -> None:
    # These names arrive from a request body, so they are not trusted input.
    with pytest.raises(KeyError):
        parse_delimited("a|b", field="pipe", row="newline")
