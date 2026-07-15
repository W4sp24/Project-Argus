"""Tests for note + task-line CRUD endpoints (thin HTTP layer over writer)."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> tuple[TestClient, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault, capture_output=True)
    (vault / "00-Inbox").mkdir()
    (vault / "00-Inbox" / "note.md").write_text("hello\n", encoding="utf-8")
    (vault / "20-Projects").mkdir()
    (vault / "20-Projects" / "p.md").write_text("- [ ] task one 📅 2026-07-20\n", encoding="utf-8")
    settings = Settings(_vault_path=vault)
    return TestClient(create_app(settings, chat_runner=lambda m: iter(()))), vault


def test_get_note_content(client):
    api, _ = client
    response = api.get("/api/note", params={"path": "00-Inbox/note.md"})
    assert response.status_code == 200
    assert response.json() == {"path": "00-Inbox/note.md", "content": "hello\n"}


def test_get_note_forbidden_and_missing(client):
    api, _ = client
    assert api.get("/api/note", params={"path": "99-Private/x.md"}).status_code == 403
    assert api.get("/api/note", params={"path": "00-Inbox/ghost.md"}).status_code == 404


def test_put_note_cas_and_conflict(client):
    api, vault = client
    ok = api.put(
        "/api/note",
        json={"path": "00-Inbox/note.md", "expected_content": "hello\n", "new_content": "hi\n"},
    )
    assert ok.status_code == 200
    assert (vault / "00-Inbox" / "note.md").read_text(encoding="utf-8") == "hi\n"
    stale = api.put(
        "/api/note",
        json={"path": "00-Inbox/note.md", "expected_content": "hello\n", "new_content": "x\n"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_content"] == "hi\n"


def test_create_note(client):
    api, vault = client
    response = api.post(
        "/api/note/create",
        json={"path": "00-Inbox/2026-07-16-idea.md", "content": "# Idea\n\nbody\n"},
    )
    assert response.status_code == 201
    assert response.json() == {"path": "00-Inbox/2026-07-16-idea.md", "content": "# Idea\n\nbody\n"}
    saved = (vault / "00-Inbox" / "2026-07-16-idea.md").read_text(encoding="utf-8")
    assert saved == "# Idea\n\nbody\n"


def test_create_note_conflict_on_existing(client):
    api, _ = client
    response = api.post(
        "/api/note/create",
        json={"path": "00-Inbox/note.md", "content": "clobber\n"},
    )
    assert response.status_code == 409


def test_create_note_forbidden_zone(client):
    api, _ = client
    response = api.post(
        "/api/note/create",
        json={"path": "99-Private/secret.md", "content": "x\n"},
    )
    assert response.status_code == 403


def test_delete_note(client):
    api, vault = client
    response = api.request("DELETE", "/api/note", params={"path": "00-Inbox/note.md"})
    assert response.status_code == 200
    assert not (vault / "00-Inbox" / "note.md").exists()


def test_toggle_update_delete_task_line(client):
    api, vault = client
    toggled = api.post(
        "/api/tasks/toggle",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] task one 📅 2026-07-20"},
    )
    assert toggled.status_code == 200
    new_line = toggled.json()["new_line"]
    assert new_line.startswith("- [x] task one")

    edited = api.post(
        "/api/tasks/line/update",
        json={
            "path": "20-Projects/p.md",
            "line": 1,
            "old_line": new_line,
            "new_line": "- [ ] task one 📅 2026-07-25",
        },
    )
    assert edited.status_code == 200

    deleted = api.post(
        "/api/tasks/line/delete",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] task one 📅 2026-07-25"},
    )
    assert deleted.status_code == 200
    assert (vault / "20-Projects" / "p.md").read_text(encoding="utf-8").strip() == ""


def test_task_line_conflict_is_409(client):
    api, _ = client
    response = api.post(
        "/api/tasks/toggle",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] something stale"},
    )
    assert response.status_code == 409
