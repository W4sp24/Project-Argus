"""Seed deterministic flashcard decks into the e2e vault's database.

Mirrors seed_suggestion.py's pattern (a direct DB row, no model call needed).
CS000 no longer ships as a sample course (backend/cli.py stopped seeding it —
that was the "study still retains sample data after it is deleted" bug), so
the e2e vault now provisions its own course.md and flashcards fixtures
directly; this script owns the flashcards-side decks.

Two decks, not two cards in one. Playwright runs this suite with `workers: 1`
against a single shared vault, so the grading in "flashcard flip and grading
advance the mock study session" schedules its card out of the due queue for
every later test. A notation card sitting behind it in the same deck would be
reachable or not depending on which tests ran first. Its own deck is selected
by name and is therefore order-independent.
"""

import json
import sys
from pathlib import Path

from backend.core.db import connect, init_schema

vault = Path(sys.argv[1])
conn = connect(vault / ".argus" / "argus.db")
init_schema(conn)


def seed(title: str, cards: list[dict[str, str]]) -> int:
    """One deck. Two-step insert (blank cards_json, then filled) mirrors
    backend/features/flashcards/store.py::generate_deck, so card ids embed the
    real deck id the way a generated deck's would."""
    cursor = conn.execute(
        "INSERT INTO flashcard_decks (course, title, cards_json) VALUES (?, ?, ?)",
        ("CS000", title, "[]"),
    )
    conn.commit()
    deck_id = int(cursor.lastrowid)
    filled = [dict(card, id=f"{deck_id}:{i}") for i, card in enumerate(cards)]
    conn.execute(
        "UPDATE flashcard_decks SET cards_json = ? WHERE id = ?", (json.dumps(filled), deck_id)
    )
    conn.commit()
    return deck_id


# Deliberately byte-identical to what it has always been: study.spec.ts asserts
# on this text and grades it, and that assertion is about the session flow.
plain = seed(
    "CS000 flashcards",
    [{"front": "What is Big-O of binary search?", "back": "O(log n)"}],
)

# The rendering fixture. Maths on both faces, since they are separate elements
# and only one of them is ever the one a regression would break.
notation = seed(
    "CS000 notation",
    [
        {
            "front": r"What does $\nabla f(x) = 0$ identify?",
            "back": r"A stationary point: $\frac{\partial f}{\partial x_i} = 0$ for every $i$.",
        }
    ],
)

conn.close()
print(f"seeded decks #{plain}, #{notation}")
