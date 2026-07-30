"""Tests for the FastAPI application."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
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


def test_no_scheduler_factory_never_spawns_background_threads(vault: Path) -> None:
    """``create_app`` without ``scheduler_factory`` must not start the
    auto-index/watch thread — the convention every test app relies on to stay
    fast and not touch chromadb/the embedding model. Runs the lifespan for
    real (not just constructing the app) via TestClient's context manager,
    since that is the only way the gating in ``lifespan`` is actually exercised.
    """
    app = create_app(Settings(_vault_path=vault))
    before = threading.active_count()
    # Compared after exiting, not during: entering TestClient's context always
    # spins up its own transient "asyncio-portal-*" thread to run the ASGI app,
    # which has nothing to do with this app's lifespan and would make an
    # in-context comparison off by one regardless of what lifespan does.
    with TestClient(app):
        pass
    assert threading.active_count() == before


def test_scheduler_factory_present_spawns_a_thread_but_survives_no_vault() -> None:
    """A machine with no VAULT_PATH configured must not crash on boot even
    when the (production-only) auto-index thread is enabled — ConfigError from
    an unconfigured vault must be caught inside the thread, not propagate."""
    app = create_app(Settings(), scheduler_factory=lambda _settings: None)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # Give the background thread a moment to hit ConfigError and return —
        # it must not linger or crash the process either way.
        time.sleep(0.2)
