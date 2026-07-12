"""Tests for agenda merge, task board, and capture routing (connectors mocked)."""

import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.connectors.gcal import CalendarEvent
from backend.main import create_app
from backend.tasks.parser import TaskItem

TODAY = date.today().isoformat()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10-Daily").mkdir(parents=True)
    (root / "10-Daily" / "today.md").write_text(
        f"- [ ] Vault task due today 📅 {TODAY} ⏫\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(
        gcal,
        "list_events",
        lambda day, service=None: [
            CalendarEvent(title="Standup", start=f"{day}T09:00:00", end=f"{day}T09:15:00")
        ],
    )
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(
        todoist,
        "list_tasks",
        lambda api=None: [TaskItem(text="Todoist errand", due=TODAY, source="todoist")],
    )
    monkeypatch.setattr(todoist, "configured", lambda: False)

    return TestClient(create_app(Settings(_vault_path=vault)))


def test_agenda_merges_events_vault_and_todoist(client: TestClient) -> None:
    payload = client.get("/api/agenda").json()

    assert payload["events"][0]["title"] == "Standup"
    texts = [task["text"] for task in payload["tasks"]]
    assert "Vault task due today" in texts
    assert "Todoist errand" in texts
    sources = {task["source"] for task in payload["tasks"]}
    assert sources == {"vault", "todoist"}
    assert payload["configured"] == {"gcal": True, "todoist": False}
    assert len(payload["top_tasks"]) <= 3


def test_tasks_board_buckets(client: TestClient) -> None:
    board = client.get("/api/tasks").json()
    today_texts = [task["text"] for task in board["today"]]
    assert "Vault task due today" in today_texts
    assert "Todoist errand" in today_texts


def test_capture_goes_through_writer_only(
    client: TestClient, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    import backend.tasks.api as tasks_api

    real = tasks_api.append_capture

    def spy(vault_path, text):
        calls.append(text)
        return real(vault_path, text)

    monkeypatch.setattr(tasks_api, "append_capture", spy)

    response = client.post("/api/capture", json={"text": "capture through writer"})

    assert response.status_code == 200
    assert calls == ["capture through writer"], "capture must route through backend.writer"
    captured = vault / response.json()["path"]
    assert "capture through writer" in captured.read_text(encoding="utf-8")


def test_capture_rejects_empty(client: TestClient) -> None:
    assert client.post("/api/capture", json={"text": "   "}).status_code == 422
