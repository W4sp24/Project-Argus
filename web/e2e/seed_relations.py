"""Seed one pre-relationships generated note into the e2e vault.

Written as a file rather than through the ingest route on purpose. The e2e
suite runs against a real backend, so asking it to *generate* a note would
make the run depend on a live model — which is exactly why
``ingest.spec.ts`` leaves every summary instruction empty. Relinking needs no
generator at all, so it is the half of this feature a black-box test can
actually reach.

The note below is byte-for-byte the shape Argus wrote before
``backend/vault/relations.py`` existed: seven frontmatter keys, a body, and
one trailing wikilink with the source's extension stripped. That last line is
the bug the feature fixes — for a PDF it resolves to nothing — so seeding it
verbatim is what makes ``relations.spec.ts`` a regression test rather than a
smoke test.
"""

import sys
from pathlib import Path

vault = Path(sys.argv[1])

course = vault / "15-Courses" / "CS000"
(course / "materials").mkdir(parents=True, exist_ok=True)
(course / "notes").mkdir(parents=True, exist_ok=True)

# A real file for the note's `source` to point at. Markdown rather than a PDF
# so the vault stays text-only and the file is indexable.
(course / "materials" / "wk1-graphs.md").write_text(
    "# Week 1 — Graphs\n\n"
    "A graph is a set of vertices and the edges between them. Breadth-first "
    "search explores a graph level by level using a queue.\n",
    encoding="utf-8",
)

# The pre-feature note shape. No tags, no topics, no related, and a trailing
# `[[wk1-graphs]]` that names no path.
(course / "notes" / "wk1-graphs.notes.md").write_text(
    "---\n"
    "course: CS000\n"
    "generated_by: argus\n"
    "note_style: summary\n"
    "prompt: ''\n"
    "source: 15-Courses/CS000/materials/wk1-graphs.md\n"
    "title: wk1-graphs — summary\n"
    "type: note\n"
    "---\n\n"
    "Graphs are vertices and edges; BFS explores them level by level.\n\n"
    "## Key points\n\n"
    "- A graph is a set of vertices and the edges between them.\n"
    "- Breadth-first search uses a queue and explores level by level.\n\n"
    "[[wk1-graphs]]\n",
    encoding="utf-8",
)

# A note the user wrote, with no `generated_by`. The relink guard must leave
# it exactly as it is, which is the property most worth pinning end to end.
(course / "notes" / "my-own-notes.md").write_text(
    "---\ntitle: my own notes\n---\n\nHand written. Do not touch.\n",
    encoding="utf-8",
)

print("seeded 1 generated note, 1 hand-written note, 1 material")
