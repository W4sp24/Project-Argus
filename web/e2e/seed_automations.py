"""Seed n8n automations state directly into a throwaway vault -- no keyring
needed, no live n8n call made.

F8: the automations e2e specs test rendering, state and layout, never the
connect-an-instance flow. The CI `e2e` job deliberately installs no
`keyrings.alt` (an unreadable keyring is a state real users reach), so
registering an n8n instance or issuing an external token through the UI would
write a credential and fail there even though it passes locally -- see
stub-n8n.mjs's docstring and automations.spec.ts's own header comment for the
full reasoning. This script sidesteps the UI entirely:

- The instance **registry** (``.argus/automations.json``) is a plain JSON
  list beside the sqlite db -- see backend.features.automations.store's
  module docstring. Writing it needs no keyring; only *resolving* a stored
  instance's API key does, and nothing seeded here is ever run against real
  n8n, so an unresolvable key is fine.
- Widgets, the cached workflow ("action") rows, runs and activity events are
  plain ``sqlite3`` CRUD via backend.features.automations.store, mirroring
  seed_suggestion.py's direct-DB-row shape.

Two instances are registered (alpha-n8n, bravo-n8n) so origin chips and the
``(instance_id, slug)`` composite key are exercised: the "calendar" widget
slug is pushed by both, and must render as two distinct rows/cards, not one
overwriting the other.

Widget coverage, one of each state (store.widget_state's four-way split):
  - "calendar" on both instances -- LIVE (fresh push, non-empty payload).
  - "tasks" on alpha              -- STALE (pushed fine, but long past its
                                      declared cadence -- last good data must
                                      still render).
  - "inbox" on bravo               -- EMPTY (fresh push, zero items).
  - "weather" on alpha             -- WAITING (installed, never pushed --
                                      last_seen_at is NULL, which upsert_widget
                                      cannot express, hence the raw INSERT
                                      below).

Two cached "action" workflows (one form, one button -- same shapes
stub-n8n.mjs's own fixtures use) give the ACTIVE tab something in its action
half too, each with one finished run so RegisteredList's last-run text and
ActionStateBadge both have real data to show.
"""

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.core.db import connect, init_schema
from backend.features.automations import store

vault = Path(sys.argv[1])
argus_dir = vault / ".argus"
conn = connect(argus_dir / "argus.db")
init_schema(conn)

now = datetime.now(UTC)


def frozen(at: datetime):
    """A `now` callable for store.py's clock-injection parameter, fixed at `at`."""
    return lambda: at


def iso(at: datetime) -> str:
    return at.isoformat()


# --- instance registry -------------------------------------------------
#
# A plain list, written straight to disk -- store.save_instances never
# touches the keyring. Each entry's key_ref points at a keyring secret that
# was never stored, so key_state/has_key resolve to absent (or unknown, on a
# machine with no working keyring backend at all) -- exactly the state a real
# "registered but the key went missing" instance would be in. Nothing seeded
# by this script is ever fired, so that never matters to a spec.

INSTANCE_A = {
    "id": "e2e-inst-alpha",
    "name": "alpha-n8n",
    "kind": "LOCAL",
    "base_url": "http://127.0.0.1:5679",
    "key_ref": store.key_ref_for("alpha-n8n"),
}
INSTANCE_B = {
    "id": "e2e-inst-bravo",
    "name": "bravo-n8n",
    "kind": "REMOTE",
    "base_url": "http://127.0.0.1:5680",
    "key_ref": store.key_ref_for("bravo-n8n"),
}
store.save_instances(argus_dir / "automations.json", [INSTANCE_A, INSTANCE_B])

# --- widgets -------------------------------------------------------------

# LIVE: fresh push, within its declared 15-minute cadence, on both instances
# -- the same slug, two instances, so the composite (instance_id, slug) key
# and the origin chip both get exercised for real.
store.upsert_widget(
    conn,
    "calendar",
    "timeline",
    {
        "entries": [
            # `end` and `all_day` are what give PLANNER.TIMELINE a duration to
            # render; without them every n8n event is zero-length.
            {
                "at": iso(now + timedelta(hours=1)),
                "end": iso(now + timedelta(hours=2)),
                "text": "Team sync",
                "sub": "Conf room A",
                "all_day": False,
            },
            {
                "at": iso(now + timedelta(hours=3)),
                "end": iso(now + timedelta(hours=4)),
                "text": "1:1 with manager",
                "all_day": False,
            },
        ]
    },
    title="Upcoming events",
    expected_interval_seconds=900,
    instance_id=INSTANCE_A["id"],
    now=frozen(now),
)
store.upsert_widget(
    conn,
    "calendar",
    "timeline",
    {"entries": [{"at": iso(now + timedelta(hours=2)), "text": "Standup"}]},
    title="Upcoming events",
    expected_interval_seconds=900,
    instance_id=INSTANCE_B["id"],
    now=frozen(now),
)

# STALE: pushed fine, but last_seen_at is 3 hours old against a 15-minute
# (900s) cadence -- well past widget_state's 2.5x-cadence cutoff. The payload
# is deliberately non-empty: STALE must still show its last good data, not
# hide it.
store.upsert_widget(
    conn,
    "tasks",
    "list",
    {
        "items": [
            # `due`/`priority`/`id` are what TASKS.DUE groups, colours, and
            # completes by. Without `due` the agenda filters an external task
            # out entirely; without `id` its checkbox can never be enabled.
            {
                "text": "Reply to registrar",
                "sub": "due today",
                "due": now.date().isoformat(),
                "priority": "highest",
                "id": "td-1",
                "href": "https://app.todoist.com/app/task/td-1",
            },
            {"text": "File expense report", "id": "td-2", "tags": ["admin"]},
        ]
    },
    title="Active tasks",
    expected_interval_seconds=900,
    instance_id=INSTANCE_A["id"],
    now=frozen(now - timedelta(hours=3)),
)

# EMPTY: a fresh push (within cadence), genuinely zero items.
store.upsert_widget(
    conn,
    "inbox",
    "list",
    {"items": []},
    title="Inbox zero",
    expected_interval_seconds=1800,
    instance_id=INSTANCE_B["id"],
    now=frozen(now),
)

# WAITING: installed, never pushed -- last_seen_at is NULL. upsert_widget
# always stamps last_seen_at from its `now` clock, so there is no way to
# express "never pushed" through it; this is the one row inserted directly.
_position = store.next_position(conn)
conn.execute(
    "INSERT INTO automation_widgets "
    "(slug, instance_id, title, kind, payload, last_seen_at, expected_interval_seconds, "
    "created_at, position, pinned, hidden) "
    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, 0)",
    (
        "weather",
        INSTANCE_A["id"],
        "Current temperature",
        "metric",
        json.dumps({"label": "Now", "value": "--"}),
        1800,
        iso(now),
        _position,
    ),
)
conn.commit()

# --- cached "action" workflows -------------------------------------------
#
# The ACTIVE tab's other half: workflows discovered via a live refresh
# normally, but that refresh needs a resolvable API key against a real n8n --
# exactly the thing this seeder exists to avoid. Writing straight to the
# workflow cache (store.upsert_workflow) is the same table a real refresh
# would populate, so RegisteredList renders identically either way.

FORM_DEF = {
    "id": "wf-echo-seed",
    "name": "E2E Echo (seeded)",
    "active": True,
    "tags": [{"id": "1", "name": "argus"}],
    "nodes": [
        {
            "name": "On form submission",
            "type": "n8n-nodes-base.formTrigger",
            "webhookId": "echo-hook-seed",
            "parameters": {
                "responseMode": "lastNode",
                "formFields": {
                    "values": [
                        {
                            "fieldLabel": "topic",
                            "fieldType": "text",
                            "requiredField": True,
                            "placeholder": "what to look up",
                        }
                    ]
                },
            },
        }
    ],
    "connections": {},
}
store.upsert_workflow(
    conn,
    "wf-echo-seed",
    name=FORM_DEF["name"],
    tags=["argus"],
    schema_json=FORM_DEF,
    active=True,
    instance_id=INSTANCE_A["id"],
    now=frozen(now),
)

BUTTON_DEF = {
    "id": "wf-button-seed",
    "name": "E2E Button (seeded)",
    "active": True,
    "tags": [{"id": "1", "name": "argus"}],
    "nodes": [
        {
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "parameters": {"path": "button-hook-seed", "responseMode": "responseNode"},
        }
    ],
    "connections": {},
}
store.upsert_workflow(
    conn,
    "wf-button-seed",
    name=BUTTON_DEF["name"],
    tags=["argus"],
    schema_json=BUTTON_DEF,
    active=True,
    instance_id=INSTANCE_B["id"],
    now=frozen(now),
)

# --- installed action workflows --------------------------------------------
#
# `GET /automations/actions` resolves these by matching the cached workflow
# *name* against the bundled template's name, so the names below must stay in
# lockstep with templates/*.json's own `"name"`. They are what make TASKS.DUE's
# quick-add write to Todoist and PLANNER.TIMELINE's `+ EVENT` appear at all;
# without them both cards fall back to their no-n8n behaviour.

TODOIST_INSERT_DEF = {
    "id": "wf-todoist-insert-seed",
    "name": "Argus: Add Todoist Task",
    "active": True,
    "tags": [{"id": "1", "name": "argus"}],
    "nodes": [
        {
            "name": "New Task Form",
            "type": "n8n-nodes-base.formTrigger",
            "webhookId": "argus-todoist-insert",
            "parameters": {
                "path": "argus-todoist-insert",
                "responseMode": "lastNode",
                "formFields": {
                    "values": [
                        {"fieldLabel": "Task", "fieldType": "text", "requiredField": True},
                        {"fieldLabel": "Due", "fieldType": "date"},
                        {"fieldLabel": "Priority", "fieldType": "dropdown"},
                    ]
                },
            },
        }
    ],
    "connections": {},
}
store.upsert_workflow(
    conn,
    TODOIST_INSERT_DEF["id"],
    name=TODOIST_INSERT_DEF["name"],
    tags=["argus"],
    schema_json=TODOIST_INSERT_DEF,
    active=True,
    instance_id=INSTANCE_A["id"],
    now=frozen(now),
)

TODOIST_COMPLETE_DEF = {
    "id": "wf-todoist-complete-seed",
    "name": "Argus: Complete Todoist Task",
    "active": True,
    "tags": [{"id": "1", "name": "argus"}],
    "nodes": [
        {
            "name": "Complete Task Webhook",
            "type": "n8n-nodes-base.webhook",
            "webhookId": "argus-todoist-complete",
            "parameters": {"path": "argus-todoist-complete", "responseMode": "responseNode"},
        }
    ],
    "connections": {},
}
store.upsert_workflow(
    conn,
    TODOIST_COMPLETE_DEF["id"],
    name=TODOIST_COMPLETE_DEF["name"],
    tags=["argus"],
    schema_json=TODOIST_COMPLETE_DEF,
    active=True,
    instance_id=INSTANCE_A["id"],
    now=frozen(now),
)

CALENDAR_INSERT_DEF = {
    "id": "wf-calendar-insert-seed",
    "name": "Argus: Add Calendar Event",
    "active": True,
    "tags": [{"id": "1", "name": "argus"}],
    "nodes": [
        {
            "name": "New Event Form",
            "type": "n8n-nodes-base.formTrigger",
            "webhookId": "argus-calendar-insert",
            "parameters": {
                "path": "argus-calendar-insert",
                "responseMode": "lastNode",
                "formFields": {
                    "values": [
                        {"fieldLabel": "Title", "fieldType": "text", "requiredField": True},
                        {"fieldLabel": "Start", "fieldType": "date", "requiredField": True},
                        {"fieldLabel": "End", "fieldType": "date", "requiredField": True},
                    ]
                },
            },
        }
    ],
    "connections": {},
}
store.upsert_workflow(
    conn,
    CALENDAR_INSERT_DEF["id"],
    name=CALENDAR_INSERT_DEF["name"],
    tags=["argus"],
    schema_json=CALENDAR_INSERT_DEF,
    active=True,
    instance_id=INSTANCE_A["id"],
    now=frozen(now),
)

# One finished run each, so RegisteredList's "last run … ok/failed" line and
# ActionStateBadge (READY / FAILING) both have real data instead of the
# never-run default.
run_ok = str(uuid.uuid4())
store.record_run_started(
    conn,
    run_ok,
    "wf-echo-seed",
    workflow_name=FORM_DEF["name"],
    instance_id=INSTANCE_A["id"],
    now=frozen(now - timedelta(minutes=10)),
)
store.finish_run(
    conn, run_ok, "ok", mode="ack", now=frozen(now - timedelta(minutes=10, seconds=-2))
)

run_failed = str(uuid.uuid4())
store.record_run_started(
    conn,
    run_failed,
    "wf-button-seed",
    workflow_name=BUTTON_DEF["name"],
    instance_id=INSTANCE_B["id"],
    now=frozen(now - timedelta(minutes=5)),
)
store.finish_run(
    conn,
    run_failed,
    "failed",
    mode=None,
    message="n8n returned 500",
    now=frozen(now - timedelta(minutes=5, seconds=-1)),
)

# --- activity events -------------------------------------------------------
#
# A spread across RUN / PUSH / FAIL / INSTALL, both instances, for the
# ACTIVITY tab and its tag filters.

store.record_event(
    conn,
    tag="RUN",
    text="completed in 2.0s",
    instance_id=INSTANCE_A["id"],
    subject="E2E Echo (seeded)",
    now=frozen(now - timedelta(minutes=10)),
)
store.record_event(
    conn,
    tag="PUSH",
    text="pushed 2 tasks",
    instance_id=INSTANCE_A["id"],
    subject="tasks",
    now=frozen(now - timedelta(hours=3)),
)
store.record_event(
    conn,
    tag="FAIL",
    text="n8n returned 500",
    instance_id=INSTANCE_B["id"],
    subject="E2E Button (seeded)",
    now=frozen(now - timedelta(minutes=5)),
)
store.record_event(
    conn,
    tag="INSTALL",
    text="installed as workflow wf-installed-1",
    instance_id=INSTANCE_B["id"],
    subject="Argus: Weather -> Metric Widget",
    now=frozen(now - timedelta(days=1)),
)
store.record_event(
    conn,
    tag="PUSH",
    text="pushed 2 calendar entries",
    instance_id=INSTANCE_B["id"],
    subject="calendar",
    now=frozen(now - timedelta(minutes=1)),
)
store.record_event(
    conn,
    tag="RUN",
    text="completed in 0.8s",
    instance_id=INSTANCE_A["id"],
    subject="E2E Echo (seeded)",
    now=frozen(now),
)

conn.close()
print(
    "seeded automations: 2 instances, 5 widgets (LIVE x2, STALE, EMPTY, WAITING), "
    "5 workflows (2 runnable + 3 actions), 6 events"
)
