"""Tests for the n8n automations data layer (JSON registry + sqlite half)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.automations import store


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "automations.db")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _clock(dt: datetime):
    """A ``now`` callable that always returns ``dt`` — the fixed-clock pattern."""
    return lambda: dt


# --- instance registry (JSON) ----------------------------------------------


def test_load_instances_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert store.load_instances(tmp_path / "automations.json") == []


def test_save_then_load_instances_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    entries = [
        {"id": "a1", "kind": "LOCAL", "name": "home", "base_url": "https://n8n.example.com",
         "key_ref": "n8n:home"},
        {"id": "b2", "kind": "CLOUD", "name": "work", "base_url": "https://work.n8n.cloud",
         "key_ref": "n8n:work"},
    ]

    store.save_instances(path, entries)

    assert store.load_instances(path) == entries


def test_save_instances_empty_list_clears_the_registry(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    store.save_instances(path, [{"id": "a1", "kind": "LOCAL", "name": "home"}])
    assert store.load_instances(path) != []

    store.save_instances(path, [])

    assert store.load_instances(path) == []


def test_key_ref_for_shape() -> None:
    assert store.key_ref_for("home") == "n8n:home"


def test_load_instances_tolerates_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    path.write_text("{{{ not json", encoding="utf-8")

    assert store.load_instances(path) == []
    # Quarantined, so the next save cannot bury what the user registered.
    assert not path.exists()
    assert (tmp_path / "automations.json.corrupt").exists()


def test_load_instances_rejects_non_dict_entries(tmp_path: Path) -> None:
    """A list of non-dict junk is treated like "no instances", not a crash."""
    path = tmp_path / "automations.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert store.load_instances(path) == []


def test_load_instances_empty_dict_means_none_registered(tmp_path: Path) -> None:
    """``{}`` is the legacy "nothing registered" sentinel, not a corrupt/garbage file."""
    path = tmp_path / "automations.json"
    path.write_text("{}", encoding="utf-8")

    assert store.load_instances(path) == []


def test_load_instances_self_heals_legacy_single_dict(tmp_path: Path) -> None:
    """A pre-multi-instance file (a single dict) upgrades to a one-element list.

    The upgraded entry gains an ``id`` and a ``kind`` it didn't have before,
    and the upgrade is written back to disk immediately — not just returned
    in memory — so every subsequent read sees the list shape too.
    """
    path = tmp_path / "automations.json"
    legacy = {"name": "home", "base_url": "https://n8n.example.com", "key_ref": "n8n:home"}
    path.write_text(json.dumps(legacy), encoding="utf-8")

    instances = store.load_instances(path)

    assert len(instances) == 1
    entry = instances[0]
    assert entry["name"] == "home"
    assert entry["base_url"] == "https://n8n.example.com"
    assert isinstance(entry["id"], str) and entry["id"]
    assert entry["kind"] == "LOCAL"

    # The file on disk was rewritten to the list shape, not left as a dict.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(on_disk, list)
    assert on_disk == instances


def test_load_instances_self_heal_preserves_existing_key_ref(tmp_path: Path) -> None:
    """The migrated entry's key_ref is untouched — no orphaned keyring secret."""
    path = tmp_path / "automations.json"
    legacy = {"name": "home", "base_url": "https://n8n.example.com", "key_ref": "n8n:home"}
    path.write_text(json.dumps(legacy), encoding="utf-8")

    entry = store.load_instances(path)[0]

    assert entry["key_ref"] == "n8n:home"
    assert entry["key_ref"] == store.key_ref_for("home")


# --- widgets: upsert / get / list -------------------------------------------


def test_upsert_widget_inserts_a_new_row(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    widget = store.upsert_widget(
        conn,
        "inbox-count",
        "metric",
        {"value": 3},
        title="Inbox",
        expected_interval_seconds=300,
        now=_clock(t0),
    )

    assert widget["slug"] == "inbox-count"
    assert widget["title"] == "Inbox"
    assert widget["kind"] == "metric"
    assert widget["payload"] == {"value": 3}
    assert widget["last_seen_at"] == t0.isoformat()
    assert widget["created_at"] == t0.isoformat()
    assert widget["expected_interval_seconds"] == 300
    assert widget["position"] == 0
    assert widget["pinned"] is False
    assert widget["hidden"] is False


def test_upsert_widget_update_preserves_created_position_pinned_hidden(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)

    store.upsert_widget(conn, "a", "metric", {"value": 1}, now=_clock(t0))
    store.upsert_widget(conn, "b", "metric", {"value": 2}, now=_clock(t0))  # position 1
    store.set_widget_flags(conn, "b", pinned=True, hidden=True, position=9)

    updated = store.upsert_widget(
        conn, "b", "list", {"items": [1, 2]}, title="B updated", now=_clock(t1)
    )

    assert updated["created_at"] == t0.isoformat()  # preserved
    assert updated["position"] == 9  # preserved
    assert updated["pinned"] is True  # preserved
    assert updated["hidden"] is True  # preserved
    assert updated["last_seen_at"] == t1.isoformat()  # refreshed
    assert updated["kind"] == "list"
    assert updated["payload"] == {"items": [1, 2]}
    assert updated["title"] == "B updated"


def test_upsert_widget_update_retains_title_and_cadence_when_omitted(conn) -> None:
    """A push that omits the cadence must not silently disable staleness detection.

    A widget that can never go stale is the silently-empty panel this whole
    feature exists to prevent, so an omitted field retains its previous value
    rather than nulling it.
    """
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)

    store.upsert_widget(
        conn,
        "weather",
        "metric",
        {"value": "31C"},
        title="WEATHER",
        expected_interval_seconds=1800,
        now=_clock(t0),
    )

    updated = store.upsert_widget(
        conn, "weather", "metric", {"value": "29C"}, now=_clock(t1)
    )

    assert updated["title"] == "WEATHER"
    assert updated["expected_interval_seconds"] == 1800
    assert updated["payload"] == {"value": "29C"}


def test_upsert_widget_update_overrides_title_and_cadence_when_provided(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)

    store.upsert_widget(
        conn, "w", "metric", {"value": 1}, title="Old", expected_interval_seconds=60, now=_clock(t0)
    )

    updated = store.upsert_widget(
        conn,
        "w",
        "metric",
        {"value": 2},
        title="New",
        expected_interval_seconds=900,
        now=_clock(t1),
    )

    assert updated["title"] == "New"
    assert updated["expected_interval_seconds"] == 900


def test_get_widget_missing_returns_none(conn) -> None:
    assert store.get_widget(conn, "ghost") is None


def test_next_position_appends_after_the_highest(conn) -> None:
    assert store.next_position(conn) == 0
    store.upsert_widget(conn, "a", "metric", {"value": 1})
    assert store.next_position(conn) == 1
    store.upsert_widget(conn, "b", "metric", {"value": 2})
    assert store.next_position(conn) == 2


def test_list_widgets_orders_by_position_then_created_at(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    store.upsert_widget(conn, "first", "metric", {"value": 1}, now=_clock(t0))
    store.upsert_widget(conn, "second", "metric", {"value": 2}, now=_clock(t0))
    store.upsert_widget(conn, "third", "metric", {"value": 3}, now=_clock(t0))
    # Reorder "third" ahead of everything else.
    store.set_widget_flags(conn, "third", position=-1)

    slugs = [w["slug"] for w in store.list_widgets(conn)]

    assert slugs == ["third", "first", "second"]


def test_list_widgets_excludes_hidden_by_default(conn) -> None:
    store.upsert_widget(conn, "visible", "metric", {"value": 1})
    store.upsert_widget(conn, "hidden", "metric", {"value": 2})
    store.set_widget_flags(conn, "hidden", hidden=True)

    assert [w["slug"] for w in store.list_widgets(conn)] == ["visible"]
    assert {w["slug"] for w in store.list_widgets(conn, include_hidden=True)} == {
        "visible",
        "hidden",
    }


def test_set_widget_flags_updates_only_given_fields(conn) -> None:
    store.upsert_widget(conn, "a", "metric", {"value": 1})

    store.set_widget_flags(conn, "a", pinned=True)
    assert store.get_widget(conn, "a")["pinned"] is True
    assert store.get_widget(conn, "a")["hidden"] is False

    store.set_widget_flags(conn, "a", hidden=True)
    assert store.get_widget(conn, "a")["pinned"] is True  # untouched
    assert store.get_widget(conn, "a")["hidden"] is True


def test_delete_widget_removes_the_row(conn) -> None:
    store.upsert_widget(conn, "a", "metric", {"value": 1})
    store.delete_widget(conn, "a")
    assert store.get_widget(conn, "a") is None
    store.delete_widget(conn, "ghost")  # no-op, must not raise


# --- widget_state: the load-bearing state machine ---------------------------


def _row(**overrides) -> dict:
    base = {
        "slug": "w",
        "title": None,
        "kind": "list",
        "payload": {"items": [1, 2]},
        "last_seen_at": None,
        "expected_interval_seconds": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "position": 0,
        "pinned": False,
        "hidden": False,
    }
    base.update(overrides)
    return base


def test_widget_state_waiting_when_never_seen() -> None:
    row = _row(last_seen_at=None, expected_interval_seconds=60)
    assert store.widget_state(row, now=_clock(datetime(2026, 1, 1, tzinfo=UTC))) == "waiting"


def test_widget_state_null_interval_never_goes_stale() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(last_seen_at=t0.isoformat(), expected_interval_seconds=None)
    far_future = t0 + timedelta(days=3650)

    assert store.widget_state(row, now=_clock(far_future)) == "live"


def test_widget_state_exactly_at_2_5x_interval_is_not_yet_stale() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(last_seen_at=t0.isoformat(), expected_interval_seconds=100)
    exactly_at_threshold = t0 + timedelta(seconds=250)

    assert store.widget_state(row, now=_clock(exactly_at_threshold)) == "live"


def test_widget_state_just_past_2_5x_interval_is_stale() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(last_seen_at=t0.isoformat(), expected_interval_seconds=100)
    just_past_threshold = t0 + timedelta(seconds=251)

    assert store.widget_state(row, now=_clock(just_past_threshold)) == "stale"


@pytest.mark.parametrize(
    "payload",
    [
        {"items": []},
        {"rows": []},
        {"entries": []},
        {"series": []},
        {"body": ""},
        {"body": None},
    ],
)
def test_widget_state_empty_payload_is_empty_not_waiting(payload) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(kind="list", last_seen_at=t0.isoformat(), payload=payload)

    assert store.widget_state(row, now=_clock(t0)) == "empty"


def test_widget_state_metric_is_never_empty() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(kind="metric", last_seen_at=t0.isoformat(), payload={"items": []})

    assert store.widget_state(row, now=_clock(t0)) == "live"


def test_widget_state_live_with_non_empty_payload() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(kind="list", last_seen_at=t0.isoformat(), payload={"items": [1, 2]})

    assert store.widget_state(row, now=_clock(t0)) == "live"


def test_widget_state_stale_takes_priority_over_empty() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    row = _row(
        kind="list",
        last_seen_at=t0.isoformat(),
        expected_interval_seconds=10,
        payload={"items": []},
    )
    long_after = t0 + timedelta(seconds=1000)

    assert store.widget_state(row, now=_clock(long_after)) == "stale"


def test_widget_state_via_real_upsert_round_trip(conn) -> None:
    """Sanity check that widget_state also works on rows that came out of the DB."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    store.upsert_widget(conn, "w", "list", {"items": []}, now=_clock(t0))
    row = store.get_widget(conn, "w")

    assert store.widget_state(row, now=_clock(t0)) == "empty"


# --- runs --------------------------------------------------------------


def test_run_lifecycle_started_then_finished_ok(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)

    store.record_run_started(conn, "run-1", "wf-1", workflow_name="Backup", now=_clock(t0))
    runs = store.list_runs(conn)
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["started_at"] == t0.isoformat()
    assert runs[0]["finished_at"] is None

    store.finish_run(
        conn, "run-1", "ok", mode="ack", message="done", execution_id="ex-1", now=_clock(t1)
    )

    run = store.list_runs(conn)[0]
    assert run["status"] == "ok"
    assert run["finished_at"] == t1.isoformat()
    assert run["mode"] == "ack"
    assert run["message"] == "done"
    assert run["execution_id"] == "ex-1"


def test_run_lifecycle_finished_failed_with_widget_payload(conn) -> None:
    store.record_run_started(conn, "run-2", "wf-1")
    store.finish_run(
        conn, "run-2", "failed", mode="widget", message="boom", payload={"items": []}
    )

    run = store.list_runs(conn)[0]
    assert run["status"] == "failed"
    assert run["payload"] == {"items": []}


def test_list_runs_orders_newest_first(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    t2 = t0 + timedelta(minutes=2)

    store.record_run_started(conn, "run-a", "wf-1", now=_clock(t0))
    store.record_run_started(conn, "run-b", "wf-1", now=_clock(t1))
    store.record_run_started(conn, "run-c", "wf-1", now=_clock(t2))

    ids = [r["id"] for r in store.list_runs(conn)]
    assert ids == ["run-c", "run-b", "run-a"]


def test_recent_runs_for_filters_by_workflow_and_limits(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(7):
        store.record_run_started(
            conn, f"wf1-run-{i}", "wf-1", now=_clock(t0 + timedelta(minutes=i))
        )
    store.record_run_started(conn, "wf2-run-0", "wf-2", now=_clock(t0))

    recent = store.recent_runs_for(conn, "wf-1", limit=5)

    assert len(recent) == 5
    assert all(r["workflow_id"] == "wf-1" for r in recent)
    # Newest first.
    assert recent[0]["id"] == "wf1-run-6"


def test_expire_stale_runs_marks_only_rows_past_the_ttl(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    store.record_run_started(conn, "old", "wf-1", now=_clock(t0))
    store.record_run_started(conn, "recent", "wf-1", now=_clock(t0 + timedelta(seconds=590)))
    store.record_run_started(conn, "already-finished", "wf-1", now=_clock(t0))
    store.finish_run(conn, "already-finished", "ok", now=_clock(t0 + timedelta(seconds=1)))

    check_time = t0 + timedelta(seconds=600)
    expired_count = store.expire_stale_runs(conn, ttl_seconds=300, now=_clock(check_time))

    assert expired_count == 1
    by_id = {r["id"]: r for r in store.list_runs(conn)}
    assert by_id["old"]["status"] == "unresolved"
    assert by_id["recent"]["status"] == "running"
    assert by_id["already-finished"]["status"] == "ok"


# --- workflow cache: the id-collision fix -----------------------------------


def test_upsert_workflow_same_id_different_instances_does_not_collide(conn) -> None:
    """The bug _migrate_workflow_key/upsert_workflow fixes: n8n's workflow id is
    a small per-instance value, so two self-hosted instances can legitimately
    hand out the same id to two unrelated workflows. Before the (instance_id,
    id) primary key, instance B's upsert overwrote instance A's cached row.
    """
    store.upsert_workflow(
        conn, "5", name="Instance A workflow", tags=["argus"], schema_json=None,
        active=True, instance_id="instance-a",
    )
    store.upsert_workflow(
        conn, "5", name="Instance B workflow", tags=["argus"], schema_json=None,
        active=True, instance_id="instance-b",
    )

    a = store.get_workflow(conn, "5", instance_id="instance-a")
    b = store.get_workflow(conn, "5", instance_id="instance-b")

    assert a is not None and a["name"] == "Instance A workflow"
    assert b is not None and b["name"] == "Instance B workflow"
    assert {row["id"] for row in store.list_workflows(conn)} == {"5"}
    assert len(store.list_workflows(conn)) == 2


def test_upsert_workflow_same_id_same_instance_still_updates_in_place(conn) -> None:
    """The ordinary refresh case must still be a true upsert, not an insert-forever."""
    store.upsert_workflow(
        conn, "5", name="First", tags=[], schema_json=None, active=False,
        instance_id="instance-a",
    )
    store.upsert_workflow(
        conn, "5", name="Second", tags=[], schema_json=None, active=True,
        instance_id="instance-a",
    )

    rows = store.list_workflows(conn, instance_id="instance-a")
    assert len(rows) == 1
    assert rows[0]["name"] == "Second"
    assert rows[0]["active"] is True


def test_upsert_workflow_default_sentinel_instance_still_updates_in_place(conn) -> None:
    """The pre-multi-instance case (instance_id defaults to the "" sentinel for
    every caller) is still a real upsert too, not an insert-forever — this is
    exactly the case a nullable instance_id would have broken (see db.SCHEMA):
    NULL-vs-NULL is never "equal" for uniqueness purposes, but ""-vs-"" is.
    """
    store.upsert_workflow(conn, "5", name="First", tags=[], schema_json=None, active=False)
    store.upsert_workflow(conn, "5", name="Second", tags=[], schema_json=None, active=True)

    rows = store.list_workflows(conn)
    assert len(rows) == 1
    assert rows[0]["name"] == "Second"
    assert rows[0]["instance_id"] == ""


def test_workflow_row_includes_instance_id(conn) -> None:
    row = store.upsert_workflow(
        conn, "1", name="W", tags=[], schema_json=None, active=True, instance_id="inst-1"
    )
    assert row["instance_id"] == "inst-1"
    assert store.get_workflow(conn, "1", instance_id="inst-1")["instance_id"] == "inst-1"


def test_delete_missing_workflows_scoped_to_instance(conn) -> None:
    store.upsert_workflow(
        conn, "1", name="A", tags=[], schema_json=None, active=True, instance_id="a"
    )
    store.upsert_workflow(
        conn, "1", name="B", tags=[], schema_json=None, active=True, instance_id="b"
    )

    dropped = store.delete_missing_workflows(conn, [], instance_id="a")

    assert dropped == 1
    assert store.get_workflow(conn, "1", instance_id="a") is None
    assert store.get_workflow(conn, "1", instance_id="b") is not None


def test_migrate_workflow_key_from_old_schema_preserves_rows_and_prevents_collision(
    tmp_path: Path,
) -> None:
    """Simulates a database that reached ``automation_workflows`` before the
    ``(instance_id, id)`` primary key existed, and proves the guarded rebuild
    in :mod:`backend.core.db` (``_migrate_workflow_key``) both preserves the
    pre-existing row and lets a second instance reuse the same n8n id without
    colliding with it — the actual bug this migration fixes.
    """
    db_path = tmp_path / "old.db"
    conn = connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE automation_workflows (
                id           TEXT PRIMARY KEY,
                name         TEXT,
                tags         TEXT,
                schema_json  TEXT,
                active       INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO automation_workflows (id, name, tags, schema_json, active, last_seen_at) "
            "VALUES ('5', 'Legacy', '[]', NULL, 1, '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()

        init_schema(conn)  # runs the ALTER guard, then the guarded PK rebuild

        # The pre-existing row survived the rebuild, keyed under instance_id
        # "" -- the ALTER guard backfills NOT NULL DEFAULT '' for every row
        # that predates the column, and get_workflow's own default sentinel
        # is "" too, so the plain lookup below finds it.
        legacy = store.get_workflow(conn, "5")
        assert legacy is not None
        assert legacy["name"] == "Legacy"
        assert legacy["instance_id"] == ""

        # A second instance can now use the same n8n id "5" without
        # clobbering the legacy row.
        store.upsert_workflow(
            conn,
            "5",
            name="New instance workflow",
            tags=[],
            schema_json=None,
            active=True,
            instance_id="new-instance",
        )

        assert store.get_workflow(conn, "5")["name"] == "Legacy"  # untouched
        second = store.get_workflow(conn, "5", instance_id="new-instance")
        assert second is not None
        assert second["name"] == "New instance workflow"
        assert len(store.list_workflows(conn)) == 2

        init_schema(conn)  # must stay idempotent after the rebuild
    finally:
        conn.close()


# --- widgets: grid layout ----------------------------------------------


def test_widget_row_has_grid_defaults(conn) -> None:
    widget = store.upsert_widget(conn, "w", "metric", {"value": 1})
    assert widget["grid_cols"] == 1
    assert widget["grid_rows"] == 1
    assert widget["layout_locked"] is False
    assert widget["instance_id"] == ""


def test_set_widget_flags_updates_grid_layout(conn) -> None:
    store.upsert_widget(conn, "w", "metric", {"value": 1})

    store.set_widget_flags(conn, "w", grid_cols=3, grid_rows=2, layout_locked=True)

    widget = store.get_widget(conn, "w")
    assert widget["grid_cols"] == 3
    assert widget["grid_rows"] == 2
    assert widget["layout_locked"] is True


@pytest.mark.parametrize("bad_value", [0, 5, -1])
def test_set_widget_flags_rejects_out_of_range_grid_cols(conn, bad_value) -> None:
    store.upsert_widget(conn, "w", "metric", {"value": 1})
    with pytest.raises(ValueError):
        store.set_widget_flags(conn, "w", grid_cols=bad_value)


@pytest.mark.parametrize("bad_value", [0, 5, -1])
def test_set_widget_flags_rejects_out_of_range_grid_rows(conn, bad_value) -> None:
    store.upsert_widget(conn, "w", "metric", {"value": 1})
    with pytest.raises(ValueError):
        store.set_widget_flags(conn, "w", grid_rows=bad_value)


def test_set_widget_flags_grid_validation_runs_before_any_write(conn) -> None:
    """An invalid grid_cols must not partially apply other fields in the same call."""
    store.upsert_widget(conn, "w", "metric", {"value": 1})

    with pytest.raises(ValueError):
        store.set_widget_flags(conn, "w", pinned=True, grid_cols=99)

    assert store.get_widget(conn, "w")["pinned"] is False


# --- widgets/workflows: instance-scoped rows preserve legacy default ----


def test_upsert_widget_default_instance_id_is_the_sentinel(conn) -> None:
    widget = store.upsert_widget(conn, "w", "metric", {"value": 1})
    assert widget["instance_id"] == ""


def test_upsert_widget_same_slug_different_instances_does_not_collide(conn) -> None:
    """The approved design note: the same slug on two instances is two automations."""
    store.upsert_widget(conn, "w", "metric", {"value": 1}, instance_id="a")
    store.upsert_widget(conn, "w", "metric", {"value": 2}, instance_id="b")

    assert store.get_widget(conn, "w", instance_id="a")["payload"] == {"value": 1}
    assert store.get_widget(conn, "w", instance_id="b")["payload"] == {"value": 2}
    assert len(store.list_widgets(conn)) == 2


def test_automation_widgets_primary_key_actually_enforces_uniqueness(conn) -> None:
    """The regression this whole fix is about: a nullable instance_id inside a
    PRIMARY KEY enforces nothing, because SQLite never treats two NULLs as
    equal for uniqueness. ``instance_id`` is deliberately left out of both
    INSERTs below, so each falls back to the column's own schema default —
    proving the *schema's* default is the '' sentinel, not just something
    application code happens to pass. With ``NOT NULL DEFAULT ''`` both rows
    land on the same key and collide; a nullable column would default both
    to NULL and let them silently coexist.
    """
    conn.execute(
        "INSERT INTO automation_widgets (slug, kind, payload, created_at) "
        "VALUES ('weather', 'metric', '{}', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO automation_widgets (slug, kind, payload, created_at) "
            "VALUES ('weather', 'metric', '{}', '2026-01-01T00:00:01+00:00')"
        )


def test_automation_workflows_primary_key_actually_enforces_uniqueness(conn) -> None:
    """Same regression, same fix, on the other re-keyed table — see
    test_automation_widgets_primary_key_actually_enforces_uniqueness above
    for why instance_id is omitted rather than passed explicitly.
    """
    conn.execute(
        "INSERT INTO automation_workflows (id, active, last_seen_at) "
        "VALUES ('5', 1, '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO automation_workflows (id, active, last_seen_at) "
            "VALUES ('5', 1, '2026-01-01T00:00:01+00:00')"
        )


# --- events --------------------------------------------------------------


def test_record_event_round_trips(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    event = store.record_event(
        conn, tag="RUN", text="Backup ran", subject="wf-1", instance_id="inst-1", now=_clock(t0)
    )

    assert event["tag"] == "RUN"
    assert event["text"] == "Backup ran"
    assert event["subject"] == "wf-1"
    assert event["instance_id"] == "inst-1"
    assert event["ts"] == t0.isoformat()
    assert isinstance(event["id"], int)


def test_record_event_retention_cap_prunes_oldest(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    cap = store.EVENT_RETENTION_CAP
    total = cap + 5

    for i in range(total):
        store.record_event(
            conn, tag="RUN", text=f"event {i}", now=_clock(t0 + timedelta(seconds=i))
        )

    events = store.list_events(conn, limit=total)
    assert len(events) == cap
    texts = {e["text"] for e in events}
    # The oldest 5 (event 0..4) must be gone; the newest must remain.
    for i in range(5):
        assert f"event {i}" not in texts
    assert f"event {total - 1}" in texts


def test_list_events_filters_by_tag(conn) -> None:
    store.record_event(conn, tag="RUN", text="a run")
    store.record_event(conn, tag="FAIL", text="a failure")
    store.record_event(conn, tag="PUSH", text="a push")

    runs = store.list_events(conn, tag="RUN")

    assert len(runs) == 1
    assert runs[0]["text"] == "a run"


def test_list_events_filters_by_instance(conn) -> None:
    store.record_event(conn, tag="RUN", text="from a", instance_id="a")
    store.record_event(conn, tag="RUN", text="from b", instance_id="b")

    from_a = store.list_events(conn, instance_id="a")

    assert len(from_a) == 1
    assert from_a[0]["text"] == "from a"


def test_list_events_orders_newest_first(conn) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    store.record_event(conn, tag="RUN", text="first", now=_clock(t0))
    store.record_event(conn, tag="RUN", text="second", now=_clock(t0 + timedelta(seconds=1)))
    store.record_event(conn, tag="RUN", text="third", now=_clock(t0 + timedelta(seconds=2)))

    texts = [e["text"] for e in store.list_events(conn)]

    assert texts == ["third", "second", "first"]


# --- prefs -------------------------------------------------------------


def test_get_pref_default_when_unset(conn) -> None:
    assert store.get_pref(conn, "layout-custom") is None
    assert store.get_pref(conn, "layout-custom", "false") == "false"


def test_set_then_get_pref_round_trips(conn) -> None:
    store.set_pref(conn, "layout-custom", "true")
    assert store.get_pref(conn, "layout-custom") == "true"


def test_set_pref_overwrites_existing_value(conn) -> None:
    store.set_pref(conn, "layout-custom", "true")
    store.set_pref(conn, "layout-custom", "false")
    assert store.get_pref(conn, "layout-custom") == "false"


# --- ensure_instance_attribution (B3 backfill) ------------------------------


def _register(path: Path, *entries: dict) -> None:
    store.save_instances(path, list(entries))


def test_ensure_instance_attribution_backfills_when_exactly_one_instance(
    conn, tmp_path: Path
) -> None:
    """The one unambiguous case: sentinel rows move to the sole instance's real id."""
    registry = tmp_path / "automations.json"
    _register(registry, {"id": "inst-1", "kind": "LOCAL", "name": "home", "base_url": "http://x"})

    store.upsert_widget(conn, "w", "metric", {"value": 1})  # instance_id="" (sentinel)
    store.upsert_workflow(conn, "5", name="W", tags=[], schema_json=None, active=True)
    store.record_run_started(conn, "run-1", "5")
    store.record_event(conn, tag="RUN", text="ran")

    store.ensure_instance_attribution(conn, registry)

    assert store.get_widget(conn, "w", instance_id="inst-1") is not None
    assert store.get_widget(conn, "w", instance_id="") is None
    assert store.get_workflow(conn, "5", instance_id="inst-1") is not None
    assert store.get_workflow(conn, "5", instance_id="") is None
    assert store.list_runs(conn)[0]["instance_id"] == "inst-1"
    assert store.list_events(conn)[0]["instance_id"] == "inst-1"


def test_ensure_instance_attribution_leaves_sentinel_rows_alone_with_zero_instances(
    conn, tmp_path: Path
) -> None:
    """The property that protects user data: no instance registered means no
    id to assign, so sentinel rows must stay exactly as they are."""
    registry = tmp_path / "automations.json"  # never written to -- zero instances

    store.upsert_widget(conn, "w", "metric", {"value": 1})
    store.upsert_workflow(conn, "5", name="W", tags=[], schema_json=None, active=True)

    store.ensure_instance_attribution(conn, registry)

    assert store.get_widget(conn, "w", instance_id="") is not None
    assert store.get_workflow(conn, "5", instance_id="") is not None


def test_ensure_instance_attribution_leaves_sentinel_rows_alone_with_two_instances(
    conn, tmp_path: Path
) -> None:
    """The property that protects user data: with 2+ instances registered,
    guessing which one a sentinel row belongs to would silently mis-attribute
    it, so it must be left untouched — visible, but unattributed."""
    registry = tmp_path / "automations.json"
    _register(
        registry,
        {"id": "inst-1", "kind": "LOCAL", "name": "home", "base_url": "http://x"},
        {"id": "inst-2", "kind": "REMOTE", "name": "work", "base_url": "http://y"},
    )

    store.upsert_widget(conn, "w", "metric", {"value": 1})
    store.upsert_workflow(conn, "5", name="W", tags=[], schema_json=None, active=True)

    store.ensure_instance_attribution(conn, registry)

    assert store.get_widget(conn, "w", instance_id="") is not None
    assert store.get_workflow(conn, "5", instance_id="") is not None


def test_ensure_instance_attribution_is_idempotent_and_cheap_once_done(
    conn, tmp_path: Path
) -> None:
    """A second call after everything is already backfilled must not error
    (and, per the guarded UPDATEs, has nothing left to touch)."""
    registry = tmp_path / "automations.json"
    _register(registry, {"id": "inst-1", "kind": "LOCAL", "name": "home", "base_url": "http://x"})
    store.upsert_widget(conn, "w", "metric", {"value": 1})

    store.ensure_instance_attribution(conn, registry)
    store.ensure_instance_attribution(conn, registry)  # must not raise

    assert store.get_widget(conn, "w", instance_id="inst-1") is not None


def test_ensure_instance_attribution_skips_a_slug_that_already_collides(
    conn, tmp_path: Path
) -> None:
    """A sentinel widget row is left alone (not reassigned into a collision)
    when a row already occupies (real_instance_id, slug) — the NOT EXISTS
    guard, not an IntegrityError, is how that is avoided."""
    registry = tmp_path / "automations.json"
    _register(registry, {"id": "inst-1", "kind": "LOCAL", "name": "home", "base_url": "http://x"})

    store.upsert_widget(conn, "w", "metric", {"value": "sentinel"})  # instance_id=""
    store.upsert_widget(conn, "w", "metric", {"value": "real"}, instance_id="inst-1")

    store.ensure_instance_attribution(conn, registry)

    # The real-id row is untouched, and the sentinel row survives rather than
    # raising or being silently dropped.
    assert store.get_widget(conn, "w", instance_id="inst-1")["payload"] == {"value": "real"}
    assert store.get_widget(conn, "w", instance_id="")["payload"] == {"value": "sentinel"}
