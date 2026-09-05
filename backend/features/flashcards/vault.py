"""Cards in and out of the vault.

This module exists because of a specific absurdity. The old ``generate_deck``
read exactly one file, ``<courses>/<CODE>/flashcards.md``, and **nothing in
Argus ever wrote it** — so unless you hand-authored that file, every deck
attempt failed. Meanwhile every note ingest writes carries a ``Q::``/``A::``
self-test tail (``backend/agent/prompts/note_quality.md``) that no reader
existed for.

:func:`import_from_note` closes that loop by reading those pairs out of *any*
note. ``flashcards.md`` becomes one importable note among many rather than the
single privileged input.

:func:`export_deck` writes the other way, into the course's ``study/`` folder
— the one zone sanctioned to take generated output without a git snapshot per
invariant I1.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.flashcards import store
from backend.features.flashcards.parsing import parse_qa_pairs
from backend.features.flashcards.store import FlashcardsError
from backend.vault.writer import WriterForbidden, guard_user_path


def import_from_note(
    vault_path: Path,
    conn: sqlite3.Connection,
    deck_id: int,
    rel_path: str,
    *,
    taxonomy: Taxonomy | None = None,
) -> int:
    """Add every ``Q::``/``A::`` pair in one vault note to a deck.

    Returns how many cards were added. Raises :class:`FlashcardsError` if the
    note is missing, outside the vault, or carries no pairs — the last of which
    is worth its own message, because "0 cards imported" from a note the user
    picked on purpose is a question, not a result.
    """
    try:
        resolved = guard_user_path(vault_path, rel_path, taxonomy=taxonomy)
    except WriterForbidden as exc:
        # Reused rather than reimplemented: this is the same "is this path
        # really inside the vault" question every user-supplied path has to
        # answer, and it already handles Windows semantics and protected zones.
        raise FlashcardsError(str(exc)) from exc
    if not resolved.is_file():
        raise FlashcardsError(f"no note at {rel_path}")

    pairs = parse_qa_pairs(resolved.read_text(encoding="utf-8"))
    if not pairs:
        raise FlashcardsError(
            f"{rel_path} has no Q:: / A:: pairs — notes Argus generates carry them "
            "in their self-test section"
        )
    return store.add_cards(
        conn,
        deck_id,
        [{"front": front, "back": back} for front, back in pairs],
        source_path=rel_path,
    )


def deck_markdown(deck: store.DeckDetail) -> str:
    """Render a deck as ``Q::``/``A::`` markdown.

    Round-trips through :func:`parse_qa_pairs`, which is what makes export and
    import inverses rather than two formats that merely resemble each other.
    """
    lines = [f"# {deck.title}", ""]
    if deck.description:
        lines += [deck.description, ""]
    for card in deck.card_list:
        lines.append(f"Q:: {card.front}")
        lines.append(f"A:: {card.back}")
        lines.append("")
    return "\n".join(lines)


def export_deck(
    vault_path: Path,
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    taxonomy: Taxonomy | None = None,
) -> str:
    """Write a deck to its course's ``flashcards.md``. Returns the vault path.

    A deck belonging to no course has nowhere to land, and inventing a folder
    for it would be a worse answer than saying so.
    """
    tax = taxonomy or active_taxonomy()
    deck = store.load_deck(conn, deck_id)
    if not deck.course:
        raise FlashcardsError(
            f'"{deck.title}" belongs to no course, so there is no folder to export it to '
            "— set a course on the deck first"
        )
    if not deck.card_list:
        raise FlashcardsError(f'"{deck.title}" has no cards to export')

    rel_path = f"{tax.courses}/{deck.course}/flashcards.md"
    target = guard_user_path(vault_path, rel_path, taxonomy=tax)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(deck_markdown(deck), encoding="utf-8")
    return rel_path
