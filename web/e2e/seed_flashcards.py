"""Seed deterministic flashcard decks into the e2e vault's database.

Mirrors seed_suggestion.py's pattern (direct store calls, no model, no
network). CS000 no longer ships as a sample course (backend/cli.py stopped
seeding it — that was the "study still retains sample data after it is
deleted" bug), so the e2e vault provisions its own course.md and flashcards
fixtures directly; this script owns the flashcards-side decks.

Two decks, not two cards in one. Playwright runs this suite with `workers: 1`
against a single shared vault, so the grading in the review-session test
schedules its card out of the due queue for every later test. A notation card
sitting behind it in the same deck would be reachable or not depending on which
tests ran first. Its own deck is selected by name and is therefore
order-independent.

**Nothing else may be seeded here.** A startup seed is global state across a
single-worker suite: seeding a material into CS000 once broke the test that
asserts the GUIDE button is disabled *precisely because* that course has none.
Anything a single test needs, that test builds through the API.
"""

import sys
from pathlib import Path

from backend.core.db import connect, init_schema
from backend.features.flashcards import store

vault = Path(sys.argv[1])
conn = connect(vault / ".argus" / "argus.db")
init_schema(conn)


def seed(title: str, cards: list[dict[str, str]]) -> int:
    """One deck, through the real store — rows, not a JSON blob.

    Going through `store` rather than hand-writing SQL means this fixture
    cannot drift from the schema the app actually reads, which is what the
    previous two-step `cards_json` insert had started to do.
    """
    deck_id = store.create_deck(conn, title=title, course="CS000", source="imported")
    store.add_cards(conn, deck_id, cards)
    return deck_id


# Deliberately byte-identical to what it has always been: notebook.spec.ts
# asserts on this text and grades it, and that assertion is about the session
# flow rather than the content.
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
