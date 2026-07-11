"""Tests for the read-only dev-journal API (invariants D1/D2)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app

STUB = """---
type: dev-session
project: demo
date: 2026-07-10
tags: [dev-journal]
---

# 2026-07-10 - demo

## 14:00 - session abc12345

- **project:** demo
- **cwd:** `C:\\code\\demo`
- **session_id:** `abc12345`
- **branch:** `feat/thing`
- **files changed:** 3

## Narrative — 14:30

Did the thing.
"""

STUB_NO_NARRATIVE = """---
type: dev-session
project: demo
date: 2026-07-11
tags: [dev-journal]
---

## 09:00 - session def67890

- **project:** demo
- **branch:** `main`
- **files changed:** 1
"""

SECRET_SESSION = """---
type: dev-session
project: secret
date: 2026-07-11
tags: [dev-journal, no-ai]
---

Confidential experiment notes.
"""


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    projects = root / "90-Meta" / "projects"
    sessions = root / "90-Meta" / "sessions" / "2026"
    projects.mkdir(parents=True)
    sessions.mkdir(parents=True)

    (root / "Welcome.md").write_text("# Welcome\n", encoding="utf-8")
    (projects / "demo.md").write_text(
        "---\ntype: dev-project\ntags: [dev-journal]\n---\n\n# Demo Project\n\n"
        "## Open threads\n\n- [ ] one\n- [ ] two\n- [x] done\n",
        encoding="utf-8",
    )
    (projects / "secret-proj.md").write_text(
        "---\ntags: [no-ai]\n---\n\n# Hidden\n", encoding="utf-8"
    )
    (sessions / "2026-07-10-demo.md").write_text(STUB, encoding="utf-8")
    (sessions / "2026-07-11-demo.md").write_text(STUB_NO_NARRATIVE, encoding="utf-8")
    (sessions / "2026-07-11-secret.md").write_text(SECRET_SESSION, encoding="utf-8")
    return root


@pytest.fixture()
def client(vault: Path) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault)))


def test_projects_lists_metadata_and_counts(client: TestClient) -> None:
    projects = client.get("/api/journal/projects").json()
    slugs = [p["slug"] for p in projects]

    assert "demo" in slugs
    demo = next(p for p in projects if p["slug"] == "demo")
    assert demo["title"] == "Demo Project"
    assert demo["sessions"] == 2
    assert demo["open_threads"] == 2


def test_no_ai_notes_never_appear_anywhere(client: TestClient) -> None:
    projects = client.get("/api/journal/projects").json()
    assert all(p["slug"] != "secret-proj" for p in projects), "D2 violation (projects)"

    sessions = client.get("/api/journal/sessions").json()
    assert all(s["project"] != "secret" for s in sessions), "D2 violation (sessions)"

    response = client.get(
        "/api/journal/note", params={"path": "90-Meta/sessions/2026/2026-07-11-secret.md"}
    )
    assert response.status_code == 404, "D2 violation (note fetch)"


def test_sessions_parse_stub_fields(client: TestClient) -> None:
    sessions = client.get("/api/journal/sessions", params={"project": "demo"}).json()

    assert [s["date"] for s in sessions] == ["2026-07-11", "2026-07-10"]  # newest first
    newest, oldest = sessions
    assert newest["branch"] == "main"
    assert newest["files"] == 1
    assert newest["has_narrative"] is False
    assert oldest["branch"] == "feat/thing"
    assert oldest["files"] == 3
    assert oldest["has_narrative"] is True


def test_note_returns_markdown_and_obsidian_uri(client: TestClient) -> None:
    payload = client.get("/api/journal/note", params={"path": "90-Meta/projects/demo.md"}).json()

    assert "# Demo Project" in payload["markdown"]
    assert payload["obsidian_uri"].startswith("obsidian://open?vault=vault&file=90-Meta")


def test_note_path_cannot_escape_90_meta(client: TestClient, vault: Path) -> None:
    for bad in ["../Welcome.md", "90-Meta/../Welcome.md", "Welcome.md", str(vault / "Welcome.md")]:
        response = client.get("/api/journal/note", params={"path": bad})
        assert response.status_code == 400, f"path {bad!r} escaped 90-Meta/"
