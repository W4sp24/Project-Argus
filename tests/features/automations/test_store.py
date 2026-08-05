"""Tests for the n8n automations data layer (JSON registry + sqlite half)."""

from __future__ import annotations

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


def test_load_instance_missing_file_returns_none(tmp_path: Path) -> None:
    assert store.load_instance(tmp_path / "automations.json") is None


def test_save_then_load_instance_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    entry = {"name": "home", "base_url": "https://n8n.example.com", "key_ref": "n8n:home"}

    store.save_instance(path, entry)

    assert store.load_instance(path) == entry


def test_delete_instance_clears_the_registry(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    store.save_instance(path, {"name": "home"})
    assert store.load_instance(path) is not None

    store.delete_instance(path)

    assert store.load_instance(path) is None


def test_key_ref_for_shape() -> None:
    assert store.key_ref_for("home") == "n8n:home"


def test_load_instance_tolerates_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "automations.json"
    path.write_text("{{{ not json", encoding="utf-8")

    assert store.load_instance(path) is None
    # Quarantined, so the next save cannot bury what the user registered.
    assert not path.exists()
    assert (tmp_path / "automations.json.corrupt").exists()


def test_load_instance_rejects_non_dict_payload(tmp_path: Path) -> None:
    """A stray list (or any non-dict JSON) is treated like "no instance", not a crash."""
    path = tmp_path / "automations.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert store.load_instance(path) is None


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
