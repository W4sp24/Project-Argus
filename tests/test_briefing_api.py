"""Tests for briefing write path, endpoints, and the scheduler factory."""

import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.writer import write_briefing

TODAY = date.today().isoformat()


def _git_log(vault: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True, check=False
    ).stdout


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "10-Daily").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=root, capture_output=True, check=True
    )
    return root


def test_write_briefing_replaces_section_not_duplicates(vault: Path) -> None:
    before = _git_log(vault).count("\n")

    path = write_briefing(vault, "Briefing v1")
    assert path == f"10-Daily/{TODAY}.md"
    note = vault / "10-Daily" / f"{TODAY}.md"
    assert "## Briefing" in note.read_text(encoding="utf-8")
    assert "Briefing v1" in note.read_text(encoding="utf-8")

    write_briefing(vault, "Briefing v2")
    content = note.read_text(encoding="utf-8")
    assert "Briefing v2" in content
    assert "Briefing v1" not in content
    assert content.count("## Briefing") == 1
    assert _git_log(vault).count("\n") == before + 2, "each write snapshots first (I2)"


def test_write_briefing_preserves_other_sections(vault: Path) -> None:
    note = vault / "10-Daily" / f"{TODAY}.md"
    note.write_text(f"# {TODAY}\n\n## FRIDAY log\n\n- 09:00 — applied #1\n", encoding="utf-8")

    write_briefing(vault, "Good morning")

    content = note.read_text(encoding="utf-8")
    assert "## FRIDAY log" in content
    assert "- 09:00 — applied #1" in content
    assert content.index("## Briefing") < content.index("## FRIDAY log")


@pytest.fixture()
def client(vault: Path) -> TestClient:
    app = create_app(
        Settings(_vault_path=vault),
        briefing_composer=lambda data: f"Composed briefing for {data.date}",
    )
    return TestClient(app)


def test_briefing_run_and_get_roundtrip(client: TestClient, vault: Path) -> None:
    response = client.post("/api/briefing/run")
    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == f"10-Daily/{TODAY}.md"
    assert "Composed briefing" in payload["markdown"]

    note = vault / "10-Daily" / f"{TODAY}.md"
    assert "Composed briefing" in note.read_text(encoding="utf-8")

    fetched = client.get("/api/briefing")
    assert fetched.status_code == 200
    assert "Composed briefing" in fetched.json()["markdown"]


def test_briefing_get_404_before_first_run(client: TestClient) -> None:
    assert client.get("/api/briefing").status_code == 404


def test_build_scheduler_registers_jobs_without_starting() -> None:
    from backend.scheduler import build_scheduler

    scheduler = build_scheduler(Settings(_vault_path=Path("unused")))
    jobs = {job.id for job in scheduler.get_jobs()}
    assert jobs == {"morning-briefing", "nightly-task-refresh"}
    assert not scheduler.running
