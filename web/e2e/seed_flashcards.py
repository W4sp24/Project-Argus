"""Seed one deterministic flashcard deck into the e2e vault's database.

Mirrors seed_suggestion.py's pattern (a direct DB row, no model call needed).
CS000 no longer ships as a sample course (backend/cli.py stopped seeding it —
that was the "study still retains sample data after it is deleted" bug), so
the e2e vault now provisions its own course.md and flashcards fixtures
directly; this script owns the flashcards-side deck.
"""

import json
import sys
from pathlib import Path

from backend.core.db import connect, init_schema

vault = Path(sys.argv[1])
conn = connect(vault / ".argus" / "argus.db")
init_schema(conn)

# Two-step insert (blank cards_json, then filled) mirrors
# backend/features/flashcards/store.py::generate_deck, so card ids embed the
# real deck id like a generated deck's would.
cursor = conn.execute(
    "INSERT INTO flashcard_decks (course, title, cards_json) VALUES (?, ?, ?)",
    ("CS000", "CS000 flashcards", "[]"),
)
conn.commit()
deck_id = int(cursor.lastrowid)
cards = [{"id": f"{deck_id}:0", "front": "What is Big-O of binary search?", "back": "O(log n)"}]
conn.execute("UPDATE flashcard_decks SET cards_json = ? WHERE id = ?", (json.dumps(cards), deck_id))
conn.commit()
conn.close()
print(f"seeded deck #{deck_id}")
