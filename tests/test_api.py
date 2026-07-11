"""Tests for the FastAPI application."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10-Daily").mkdir(parents=True)
    (root / "99-Private").mkdir()
    (root / ".obsidian").mkdir()

    (root / "10-Daily" / "2026-07-12.md").write_text(
        "---\ntitle: Saturday Log\ntags: [daily]\n---\n\n# Saturday\n", encoding="utf-8"
    )
    (root / "untitled-note.md").write_text("Just text, no heading.\n", encoding="utf-8")
    (root / "99-Private" / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (root / ".obsidian" / "workspace.md").write_text("# App state\n", encoding="utf-8")
    return root


@pytest.fixture()
def client(vault: Path) -> TestClient:
    settings = Settings(_vault_path=vault)
    return TestClient(create_app(settings))


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_notes_lists_markdown_with_titles(client: TestClient) -> None:
    response = client.get("/api/notes")
    assert response.status_code == 200
    notes = {note["path"]: note for note in response.json()}

    daily = notes["10-Daily/2026-07-12.md"]
    assert daily["title"] == "Saturday Log"  # frontmatter wins
    assert daily["folder"] == "10-Daily"

    assert notes["untitled-note.md"]["title"] == "untitled-note"  # filename fallback


def test_notes_excludes_private_and_app_dirs(client: TestClient) -> None:
    paths = [note["path"] for note in client.get("/api/notes").json()]
    assert not any(path.startswith("99-Private") for path in paths), "I3 violation"
    assert not any(path.startswith(".obsidian") for path in paths)
