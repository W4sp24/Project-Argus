"""Seed one pending task suggestion into a (throwaway) vault's database."""

import sys
from pathlib import Path

from backend.db import connect, init_schema
from backend.suggestions import insert_suggestion

vault = Path(sys.argv[1])
conn = connect(vault / ".friday" / "friday.db")
init_schema(conn)
sid = insert_suggestion(
    conn,
    "task",
    {
        "path": "20-Projects/e2e.md",
        "line": 3,
        "old_line": "- [ ] Move the meeting 📅 2026-07-20",
        "new_line": "- [ ] Move the meeting 📅 2026-07-22 ⏫",
    },
    "E2E roundtrip: push the meeting to the 22nd",
)
conn.close()
print(f"seeded suggestion #{sid}")
