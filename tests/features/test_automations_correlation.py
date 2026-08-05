"""The async round trip, across both routers.

An ``argus:async`` workflow is fired by the *local* automations router, which
issues a run id and returns immediately. The result arrives much later as a
push to the *external* surface at ``?run={id}``. Those two halves live in
different packages, on different ports, behind different auth, and were built
separately — so neither side's own tests can prove they actually compose.

This is that proof, and it is also the regression guard for the failure the
design calls out: a run whose result never arrives must not sit in ``running``
forever pretending it might still succeed.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.external import auth
from backend.features.external.app import create_external_app

TOKEN = "integration-token"


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    (root / "20-Projects" / "p.md").write_text("# P\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture()
def settings(vault: Path) -> Settings:
    return Settings(_vault_path=vault)


@pytest.fixture()
def external(settings: Settings, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth, "verify", lambda presented: presented == TOKEN)
    return TestClient(create_external_app(settings))


def _db(settings: Settings):
    conn = connect(settings.db_path)
    init_schema(conn)
    return conn


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_an_async_run_is_resolved_by_the_later_external_push(
    settings: Settings, external: TestClient
) -> None:
    """The full correlated path: run issued here, result pushed back there."""
    conn = _db(settings)
    try:
        store.record_run_started(conn, "run-42", "wf-slow", workflow_name="Deep research")
        assert store.get_run(conn, "run-42")["status"] == "running"
    finally:
        conn.close()

    response = external.post(
        "/api/external/widget/deep-research?run=run-42",
        headers=_auth(),
        json={"widget": "text", "title": "RESEARCH", "body": "the finding"},
    )
    assert response.status_code == 200, response.text

    conn = _db(settings)
    try:
        run = store.get_run(conn, "run-42")
        assert run["status"] == "ok"
        assert run["mode"] == "widget"
        assert run["payload"]["body"] == "the finding"
        # The card was waiting for this, so it must NOT also become a
        # permanent dashboard widget — a one-off lookup should not clutter
        # the dashboard.
        assert store.get_widget(conn, "deep-research") is None
    finally:
        conn.close()


def test_a_result_that_never_arrives_expires_instead_of_hanging(
    settings: Settings,
) -> None:
    """A run nobody ever resolves must stop claiming it is still running."""
    started = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    conn = _db(settings)
    try:
        store.record_run_started(conn, "run-lost", "wf-slow", now=lambda: started)

        # Well inside the TTL: still legitimately running.
        expired = store.expire_stale_runs(
            conn, ttl_seconds=3600, now=lambda: started + timedelta(minutes=5)
        )
        assert expired == 0
        assert store.get_run(conn, "run-lost")["status"] == "running"

        # Past it: recorded as unresolved rather than left as a lie.
        expired = store.expire_stale_runs(
            conn, ttl_seconds=3600, now=lambda: started + timedelta(hours=2)
        )
        assert expired == 1
        assert store.get_run(conn, "run-lost")["status"] == "unresolved"
    finally:
        conn.close()


def test_expiry_does_not_touch_runs_that_already_finished(settings: Settings) -> None:
    started = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    conn = _db(settings)
    try:
        store.record_run_started(conn, "done", "wf", now=lambda: started)
        store.finish_run(conn, "done", "ok", mode="ack", now=lambda: started)

        store.expire_stale_runs(
            conn, ttl_seconds=60, now=lambda: started + timedelta(days=1)
        )
        assert store.get_run(conn, "done")["status"] == "ok"
    finally:
        conn.close()


def test_a_push_for_an_unknown_run_does_not_invent_one(
    settings: Settings, external: TestClient
) -> None:
    response = external.post(
        "/api/external/widget/x?run=never-existed",
        headers=_auth(),
        json={"widget": "text", "body": "hi"},
    )
    assert response.status_code == 404

    conn = _db(settings)
    try:
        assert store.list_runs(conn) == []
        assert store.get_widget(conn, "x") is None
    finally:
        conn.close()
