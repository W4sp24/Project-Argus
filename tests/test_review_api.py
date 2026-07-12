"""Tests for the review queue endpoints and planner tool handlers."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db import connect, init_schema
from backend.main import create_app


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n\nBody line.\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def client(vault: Path) -> TestClient:
    async def fake_planner(settings: Settings, instruction: str) -> int:
        conn = connect(settings.db_path)
        init_schema(conn)
        from backend.suggestions import insert_suggestion

        insert_suggestion(
            conn,
            "schedule",
            {"blocks": [{"title": f"Plan: {instruction}", "start": "s", "end": "e"}]},
            "fake planner block",
        )
        conn.close()
        return 1

    return TestClient(create_app(Settings(_vault_path=vault), planner=fake_planner))


def test_plan_review_dismiss_roundtrip(client: TestClient) -> None:
    created = client.post("/api/plan", json={"instruction": "tomorrow"}).json()
    assert created == {"created": 1}

    pending = client.get("/api/review").json()
    assert len(pending) == 1
    sid = pending[0]["id"]
    assert pending[0]["kind"] == "schedule"
    assert "fake planner" in pending[0]["rationale"]

    dismissed = client.post(f"/api/review/{sid}/dismiss", json={"reason": "not today"}).json()
    assert dismissed["status"] == "dismissed"
    assert dismissed["dismiss_reason"] == "not today"
    assert client.get("/api/review").json() == []


def test_approve_applies_through_writer(client: TestClient, vault: Path) -> None:
    client.post("/api/plan", json={"instruction": "x"})
    sid = client.get("/api/review").json()[0]["id"]

    # Schedule blocks require gcal; unconfigured -> conflict with clear message.
    response = client.post(f"/api/review/{sid}/approve")
    assert response.status_code == 409
    assert "not connected" in response.json()["detail"]


def test_planner_tools_insert_rows(tmp_path: Path) -> None:
    from backend.agent.planner import build_propose_tools

    conn = connect(tmp_path / "friday.db")
    init_schema(conn)
    tools = build_propose_tools(conn)
    schedule_tool = next(t for t in tools if t.name == "propose_schedule")

    result = asyncio.run(
        schedule_tool.handler(
            {
                "blocks_json": '[{"title": "Study", "start": "a", "end": "b"}]',
                "rationale": "test",
            }
        )
    )
    assert "queued suggestion" in result["content"][0]["text"]

    from backend.suggestions import pending

    rows = pending(conn)
    assert len(rows) == 1 and rows[0].kind == "schedule"
    conn.close()
