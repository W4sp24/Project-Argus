"""Consumers switch data source without knowing they did.

Migration step 1: every calendar/task consumer reads through
:mod:`backend.features.automations.sources`, which prefers a fresh n8n widget
and falls back to the native connector. Nothing downstream is aware of which
one answered.

That property is what turns the eventual removal of ``backend/connectors/``
into a deletion inside one module rather than a rewrite across six call sites,
so it is tested at the API boundary — through ``GET /api/agenda``, the way a
user actually experiences it — rather than only at the seam.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import sources, store
from backend.main import create_app

TODAY = date.today()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    for folder in ("10-Daily", "20-Projects"):
        (root / folder).mkdir(parents=True)
    (root / "20-Projects" / "p.md").write_text("# P\n\n- [ ] vault task\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture()
def settings(vault: Path) -> Settings:
    return Settings(_vault_path=vault)


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture(autouse=True)
def _connectors_answer(monkeypatch):
    """The native connectors return a recognisable event."""
    from backend.connectors import gcal, todoist
    from backend.connectors.gcal import CalendarEvent

    def events(day, service=None):
        return (
            [
                CalendarEvent(
                    title="FROM-NATIVE-GCAL",
                    start=f"{day.isoformat()}T09:00:00",
                    end=f"{day.isoformat()}T10:00:00",
                )
            ],
            None,
        )

    monkeypatch.setattr(gcal, "list_events_safe", events)
    monkeypatch.setattr(todoist, "list_tasks_safe", lambda api=None: ([], None))


def _db(settings: Settings):
    conn = connect(settings.db_path)
    init_schema(conn)
    return conn


def test_the_agenda_serves_the_connector_before_any_workflow_exists(
    client: TestClient,
) -> None:
    response = client.get("/api/agenda")
    assert response.status_code == 200, response.text
    assert [e["title"] for e in response.json()["events"]] == ["FROM-NATIVE-GCAL"]


def test_a_fresh_push_takes_over_with_no_consumer_change(
    client: TestClient, settings: Settings
) -> None:
    """The handover, seen from the endpoint a user actually calls."""
    conn = _db(settings)
    try:
        store.upsert_widget(
            conn,
            sources.CALENDAR_SLUG,
            "timeline",
            {
                "entries": [
                    {"at": f"{TODAY.isoformat()}T09:00:00", "text": "FROM-N8N-WORKFLOW"}
                ]
            },
            expected_interval_seconds=900,
        )
    finally:
        conn.close()

    response = client.get("/api/agenda")
    assert response.status_code == 200, response.text
    titles = [e["title"] for e in response.json()["events"]]
    assert titles == ["FROM-N8N-WORKFLOW"]
    assert "FROM-NATIVE-GCAL" not in titles


def test_a_dead_workflow_hands_back_rather_than_serving_stale_data(
    client: TestClient, settings: Settings
) -> None:
    """A push model fails silently, so the fallback has to be automatic.

    Without this, a workflow that broke last week would keep a working
    connector off the screen and the calendar would quietly show nothing.
    """
    from datetime import UTC, datetime, timedelta

    long_ago = datetime.now(UTC) - timedelta(days=3)
    conn = _db(settings)
    try:
        store.upsert_widget(
            conn,
            sources.CALENDAR_SLUG,
            "timeline",
            {"entries": [{"at": f"{TODAY.isoformat()}T09:00:00", "text": "FROM-N8N-WORKFLOW"}]},
            expected_interval_seconds=900,
            now=lambda: long_ago,
        )
    finally:
        conn.close()

    response = client.get("/api/agenda")
    assert response.status_code == 200, response.text
    assert [e["title"] for e in response.json()["events"]] == ["FROM-NATIVE-GCAL"]
