"""Seed deterministic chat threads into the e2e vault's database.

The e2e harness runs a real backend but has no live agent — `dashboard.spec.ts`
says so out loud ("No live agent in e2e: the ws will error"). That makes a real
turn useless for asserting how an *answer* renders, because no answer ever
arrives. So the transcript is written straight to the store, exactly as a
finished turn would have left it, and the spec asserts on the rendering.

Mirrors seed_flashcards.py: direct store calls, no model, no network. It goes
through `backend.features.chat.store` rather than raw SQL on purpose — if the
message or trace shape ever changes, this fixture breaks in CI instead of
quietly seeding rows the app can no longer read.

The two tool frames below are literally what `_tool_frame()` emits over the
socket (`backend/features/chat/router.py`), because that is what the router
persists into `tools_json`: the frontend folds the live stream and the restored
rows through one function, so the fixture has to be the same shape as the wire.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.core.db import connect, init_schema
from backend.features.chat import store

vault = Path(sys.argv[1])
conn = connect(vault / ".argus" / "argus.db")
init_schema(conn)


def clock(moment: datetime):
    """`store`'s injectable clock — a fixed instant, so Today/Yesterday/Earlier
    bucketing in the rail is deterministic instead of depending on when CI ran."""
    return lambda: moment


now = datetime.now(UTC)

# --- The rendering fixture: one finished turn with markdown and a trace ------

ANSWER = """## CS000 — where you stand

Two things are due this week, and the reading is the one you have not started.

| Week | Topic | Due |
| --- | --- | --- |
| 3 | Sorting | Aug 14 |
| 4 | Graphs | Aug 21 |

- [x] finish the week 3 problem set
- [ ] read the week 4 notes

Where the week 4 reading sits [15-Courses/CS000/course.md]:

- Graphs
  - Breadth-first search
  - Depth-first search

Sorting by comparison costs $O(n \\log n)$ in the average case, which falls out
of the recurrence

$$
T(n) = 2T\\!\\left(\\frac{n}{2}\\right) + O(n)
$$

once you unroll it.

The traversal you asked about is plain BFS:

```python
def bfs(graph, start):
    seen, queue = {start}, [start]
    while queue:
        node = queue.pop(0)
        for neighbour in graph[node]:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen
```

That is all from your course notes [15-Courses/CS000/course.md].
"""

TRACE = [
    {
        "type": "tool",
        "phase": "start",
        "call_id": "call_1",
        "name": "search_vault",
        "args": {"query": "CS000 week 4"},
    },
    {
        "type": "tool",
        "phase": "end",
        "call_id": "call_1",
        "name": "search_vault",
        "label": "search_vault",
        "detail": "CS000 week 4",
        "paths": ["15-Courses/CS000/course.md", "20-Projects/e2e.md"],
        "ok": True,
    },
    {
        "type": "tool",
        "phase": "start",
        "call_id": "call_2",
        "name": "read_note",
        "args": {"path": "15-Courses/CS000/course.md"},
    },
    {
        "type": "tool",
        "phase": "end",
        "call_id": "call_2",
        "name": "read_note",
        "label": "read_note",
        "detail": "15-Courses/CS000/course.md",
        "paths": ["15-Courses/CS000/course.md"],
        "ok": True,
    },
]

today = store.create_thread(conn, title="Where I stand in CS000", now=clock(now))
store.append_message(
    conn, today["id"], role="user", text="Where do I stand in CS000?", now=clock(now)
)
store.append_message(
    conn,
    today["id"],
    role="assistant",
    text=ANSWER,
    model="e2e-fixture",
    tools=TRACE,
    now=clock(now),
)

# --- Grouping fixtures: one per bucket the rail renders ----------------------

yesterday = now - timedelta(days=1)
older = now - timedelta(days=9)

second = store.create_thread(conn, title="Reading list for the week", now=clock(yesterday))
store.append_message(
    conn, second["id"], role="user", text="What should I read?", now=clock(yesterday)
)

third = store.create_thread(conn, title="Thesis outline", now=clock(older))
store.append_message(conn, third["id"], role="user", text="Draft an outline", now=clock(older))

# A course-scoped thread. `store.list_threads` deliberately returns these in
# the unscoped list too, so the rail must show it with a course badge rather
# than hide it — that behaviour is what this row exists to pin.
scoped = store.create_thread(conn, title="CS000 exam prep", course="CS000", now=clock(now))
store.append_message(
    conn, scoped["id"], role="user", text="What is on the exam?", now=clock(now)
)

conn.close()
print(f"seeded chat threads #{today['id']}, #{second['id']}, #{third['id']}, #{scoped['id']}")
