"""Cards in from any note, and out to markdown."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.flashcards import store, vault
from backend.features.flashcards.parsing import parse_qa_pairs
from backend.features.flashcards.store import FlashcardsError

# A note in the shape ingest actually writes: prose, then a self-test tail.
INGEST_NOTE = """---
type: note
course: CS201
---

# Lecture 3 — dynamic programming

Overlapping subproblems and optimal substructure.

## Self-test

Q:: what two properties make a problem suit dynamic programming
A:: overlapping subproblems and optimal substructure
Q:: what is memoisation
A:: caching subproblem results so each is computed once
"""


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "15-Courses" / "CS201").mkdir(parents=True)
    (root / "50-Reference").mkdir(parents=True)
    return root


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    return connection


def test_import_reads_the_self_test_tail_of_an_ordinary_note(
    vault_dir: Path, conn: sqlite3.Connection
) -> None:
    """The whole point: any note, not one privileged filename."""
    (vault_dir / "50-Reference" / "lecture-03.md").write_text(INGEST_NOTE, encoding="utf-8")
    deck_id = store.create_deck(conn, title="CS201")

    added = vault.import_from_note(vault_dir, conn, deck_id, "50-Reference/lecture-03.md")

    assert added == 2
    cards = store.load_deck(conn, deck_id).card_list
    assert cards[0].front.startswith("what two properties")
    # Where a card came from is recorded, so a deck can say what it was built
    # from later.
    assert cards[0].source_path == "50-Reference/lecture-03.md"


def test_import_still_reads_a_hand_written_flashcards_md(
    vault_dir: Path, conn: sqlite3.Connection
) -> None:
    (vault_dir / "15-Courses" / "CS201" / "flashcards.md").write_text(
        "Q:: big-O of merge sort\nA:: $O(n \\log n)$\n", encoding="utf-8"
    )
    deck_id = store.create_deck(conn, title="CS201", course="CS201")
    assert vault.import_from_note(vault_dir, conn, deck_id, "15-Courses/CS201/flashcards.md") == 1


def test_import_from_a_note_with_no_pairs_says_so(
    vault_dir: Path, conn: sqlite3.Connection
) -> None:
    # "0 cards imported" from a note picked on purpose is a question, not a
    # result.
    (vault_dir / "50-Reference" / "prose.md").write_text("# Just prose\n", encoding="utf-8")
    deck_id = store.create_deck(conn, title="Deck")
    with pytest.raises(FlashcardsError, match="no Q:: / A:: pairs"):
        vault.import_from_note(vault_dir, conn, deck_id, "50-Reference/prose.md")


def test_import_from_a_missing_note_says_so(vault_dir: Path, conn: sqlite3.Connection) -> None:
    deck_id = store.create_deck(conn, title="Deck")
    with pytest.raises(FlashcardsError, match="no note at"):
        vault.import_from_note(vault_dir, conn, deck_id, "50-Reference/nope.md")


@pytest.mark.parametrize(
    "escape",
    ["../outside.md", "/etc/passwd", "C:/Windows/system.ini", "50-Reference/../../outside.md"],
)
def test_import_refuses_a_path_that_leaves_the_vault(
    vault_dir: Path, conn: sqlite3.Connection, escape: str
) -> None:
    deck_id = store.create_deck(conn, title="Deck")
    with pytest.raises(FlashcardsError):
        vault.import_from_note(vault_dir, conn, deck_id, escape)


def test_export_round_trips_through_the_importer(
    vault_dir: Path, conn: sqlite3.Connection
) -> None:
    """Export and import are inverses, not two formats that merely resemble."""
    deck_id = store.create_deck(conn, title="CS201 deck", course="CS201")
    store.add_cards(
        conn,
        deck_id,
        [
            {"front": "big-O of merge sort", "back": "$O(n \\log n)$"},
            {"front": "is it stable", "back": "yes"},
        ],
    )

    rel = vault.export_deck(vault_dir, conn, deck_id)

    assert rel == "15-Courses/CS201/flashcards.md"
    written = (vault_dir / rel).read_text(encoding="utf-8")
    assert parse_qa_pairs(written) == [
        ("big-O of merge sort", "$O(n \\log n)$"),
        ("is it stable", "yes"),
    ]


def test_export_of_a_courseless_deck_explains_itself(
    vault_dir: Path, conn: sqlite3.Connection
) -> None:
    deck_id = store.create_deck(conn, title="Spanish verbs")
    store.add_cards(conn, deck_id, [{"front": "ser", "back": "to be"}])
    with pytest.raises(FlashcardsError, match="belongs to no course"):
        vault.export_deck(vault_dir, conn, deck_id)


def test_export_of_an_empty_deck_is_refused(vault_dir: Path, conn: sqlite3.Connection) -> None:
    deck_id = store.create_deck(conn, title="Empty", course="CS201")
    with pytest.raises(FlashcardsError, match="no cards"):
        vault.export_deck(vault_dir, conn, deck_id)
