"""Tests for agenda merge, task board, and capture routing (connectors mocked)."""

import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.connectors import ConnectorUnavailable
from backend.connectors.gcal import CalendarEvent
from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy
from backend.main import create_app
from backend.vault.tasks import TaskItem

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


def test_agenda_carries_undated_external_tasks(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undated external task used to be dropped outright.

    That is what an inbox item *is* in both Todoist and the n8n `tasks` widget
    — usually most of the list — so the filter did not trim the panel, it
    emptied it. The n8n path made it worse: pushed tasks arrived with no `due`
    at all, so installing the Todoist template blanked TASKS.DUE while the
    badge still read `TODOIST: VIA N8N`.
    """
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(gcal, "list_events", lambda day, service=None: [])
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(
        todoist,
        "list_tasks",
        lambda api=None: [
            TaskItem(text="Dated errand", due=TODAY, source="todoist"),
            TaskItem(text="Undated inbox item", source="todoist"),
        ],
    )
    monkeypatch.setattr(todoist, "configured", lambda: True)
    client = TestClient(create_app(Settings(_vault_path=vault)))

    texts = [task["text"] for task in client.get("/api/agenda").json()["tasks"]]

    assert "Dated errand" in texts
    assert "Undated inbox item" in texts


def test_agenda_caps_how_many_undated_external_tasks_it_carries(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connector returns every open task across every project — unlike the
    template, which limits to 25. TASKS.DUE is a dashboard panel, not an inbox."""
    from backend.connectors import gcal, todoist
    from backend.features.tasks.router import UNDATED_EXTERNAL_LIMIT

    monkeypatch.setattr(gcal, "list_events", lambda day, service=None: [])
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(
        todoist,
        "list_tasks",
        lambda api=None: [TaskItem(text=f"Item {i}", source="todoist") for i in range(200)],
    )
    monkeypatch.setattr(todoist, "configured", lambda: True)
    client = TestClient(create_app(Settings(_vault_path=vault)))

    external = [t for t in client.get("/api/agenda").json()["tasks"] if t["source"] == "todoist"]

    assert len(external) == UNDATED_EXTERNAL_LIMIT


def test_top_tasks_falls_back_to_someday_so_a_fresh_capture_is_visible(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture with no date lands in `someday`. `top_tasks` fell back only as
    far as `week`, so the task was written to the vault and then rendered
    nowhere — the quick-add looked like it had done nothing at all."""
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(gcal, "list_events", lambda day, service=None: [])
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(todoist, "list_tasks", lambda api=None: [])
    monkeypatch.setattr(todoist, "configured", lambda: False)
    # Only an undated task exists — nothing overdue, due today, or this week.
    (vault / "10-Daily" / "today.md").write_text("- [ ] Someday thing\n", encoding="utf-8")
    client = TestClient(create_app(Settings(_vault_path=vault)))

    payload = client.get("/api/agenda").json()

    assert [t["text"] for t in payload["top_tasks"]] == ["Someday thing"]


def test_tasks_board_buckets(client: TestClient) -> None:
    board = client.get("/api/tasks").json()
    today_texts = [task["text"] for task in board["today"]]
    assert "Vault task due today" in today_texts
    assert "Todoist errand" in today_texts


def test_capture_goes_through_writer_only(
    client: TestClient, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    import backend.features.tasks.router as tasks_api

    real = tasks_api.append_capture

    def spy(vault_path, text, **kwargs):
        calls.append(text)
        return real(vault_path, text, **kwargs)

    monkeypatch.setattr(tasks_api, "append_capture", spy)

    response = client.post("/api/capture", json={"text": "capture through writer"})

    assert response.status_code == 200
    assert calls == ["capture through writer"], "capture must route through backend.vault.writer"
    captured = vault / response.json()["path"]
    assert "capture through writer" in captured.read_text(encoding="utf-8")


def test_capture_lands_in_a_renamed_inbox(vault: Path) -> None:
    """The bug this branch fixes: quick capture must honour a custom taxonomy,
    not always write into 00-Inbox/."""
    settings = Settings(_vault_path=vault, taxonomy=Taxonomy(inbox="Quick Notes"))
    client = TestClient(create_app(settings))

    response = client.post("/api/capture", json={"text": "renamed inbox via the API"})

    assert response.status_code == 200
    path = response.json()["path"]
    assert path.startswith("Quick Notes/")
    assert (vault / path).is_file()
    assert not (vault / "00-Inbox").exists()


def test_capture_rejects_empty(client: TestClient) -> None:
    assert client.post("/api/capture", json={"text": "   "}).status_code == 422


# --- connector failure containment -----------------------------------------
#
# Before this fix, an uncaught `todoist.list_tasks()` / `gcal.list_events()`
# failure took down `/api/agenda` and `/api/tasks` with a bare 500 the moment
# a connected connector couldn't answer (expired token, network outage). Both
# endpoints must now degrade to 200 — the failure is reported via
# `connector_errors` on the agenda response, and via `/api/integrations` for
# the task board (whose shape must stay exactly `dict[str, list[TaskItem]]`).


def _reject(*_args, **_kwargs):
    # What the real `list_tasks`/`list_events` raise once a token is stored
    # but the API rejects it or the client library is missing — see
    # backend.connectors.ConnectorUnavailable.
    raise ConnectorUnavailable("401 Unauthorized")


def test_agenda_returns_200_with_connector_errors_when_todoist_fails(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(gcal, "list_events", lambda day, service=None: [])
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(todoist, "list_tasks", _reject)
    monkeypatch.setattr(todoist, "configured", lambda: True)

    client = TestClient(create_app(Settings(_vault_path=vault)))
    response = client.get("/api/agenda")

    assert response.status_code == 200
    payload = response.json()
    assert "todoist" in payload["connector_errors"]
    assert "Vault task due today" in [task["text"] for task in payload["tasks"]]


def test_agenda_returns_200_with_connector_errors_when_gcal_fails(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.connectors import gcal, todoist

    monkeypatch.setattr(gcal, "list_events", _reject)
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(todoist, "list_tasks", lambda api=None: [])
    monkeypatch.setattr(todoist, "configured", lambda: False)

    client = TestClient(create_app(Settings(_vault_path=vault)))
    response = client.get("/api/agenda")

    assert response.status_code == 200
    payload = response.json()
    assert "gcal" in payload["connector_errors"]
    assert payload["events"] == []


def test_tasks_board_returns_200_with_unchanged_shape_when_todoist_fails(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.connectors import todoist

    monkeypatch.setattr(todoist, "list_tasks", _reject)

    client = TestClient(create_app(Settings(_vault_path=vault)))
    response = client.get("/api/tasks")

    assert response.status_code == 200
    board = response.json()
    assert set(board.keys()) == {"overdue", "today", "week", "someday"}
    assert "Vault task due today" in [task["text"] for task in board["today"]]
