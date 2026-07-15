"""Tests for /api/ingest and /api/ingest/email (fake generator + fake index)."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app

EXTRACTION = json.dumps(
    {
        "tasks": ["review the draft", "book room for demo"],
        "dates": ["2026-07-20"],
        "contacts": ["Alice <alice@example.com>"],
        "summary": "Alice needs the draft reviewed before the demo.",
    }
)

EML_TEXT = (
    "From: Alice <alice@example.com>\n"
    "Subject: Draft review before demo\n"
    "Date: Thu, 09 Jul 2026 10:00:00 +0000\n"
    "\n"
    "Hi,\n"
    "\n"
    "- [ ] review the draft by 2026-07-20\n"
    "\n"
    "Thanks, Alice\n"
)


class FakeIndex:
    def upsert_file(self, vault_path, rel_path):
        return 3


class BrokenIndex:
    """Simulates missing [rag] extras — the pipeline import blows up."""

    def upsert_file(self, vault_path, rel_path):
        raise ImportError("No module named 'chromadb'")


async def fake_generator(prompt: str) -> str:
    return EXTRACTION


async def failing_generator(prompt: str) -> str:
    raise RuntimeError("agent unavailable")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "15-Courses" / "CS301").mkdir(parents=True)
    (root / "99-Private").mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=root, capture_output=True, check=True
    )
    return root


@pytest.fixture()
def client(vault: Path) -> TestClient:
    app = create_app(
        Settings(_vault_path=vault), generator=fake_generator, index_factory=FakeIndex
    )
    return TestClient(app)


def test_ingest_saves_to_inbox_and_indexes(client: TestClient, vault: Path) -> None:
    response = client.post(
        "/api/ingest", files={"file": ("notes.md", b"# Notes\n\nhello\n", "text/markdown")}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"path": "00-Inbox/files/notes.md", "chunks": 3, "indexed": True}
    assert (vault / "00-Inbox" / "files" / "notes.md").is_file()

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout
    assert "pre-apply snapshot" in log, "ingest must snapshot before writing (I1/I2)"


def test_ingest_into_course_folder(client: TestClient, vault: Path) -> None:
    response = client.post(
        "/api/ingest",
        data={"target": "15-Courses/CS301"},
        files={"file": ("lecture.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["path"] == "15-Courses/CS301/lecture.pdf"
    assert (vault / "15-Courses" / "CS301" / "lecture.pdf").is_file()


def test_ingest_refuses_private_zone(client: TestClient, vault: Path) -> None:
    response = client.post(
        "/api/ingest",
        data={"target": "99-Private"},
        files={"file": ("x.md", b"# x\n", "text/markdown")},
    )
    assert response.status_code == 400, "99-Private/ must never accept ingested files (I3)"
    assert not (vault / "99-Private" / "x.md").exists()


def test_ingest_rejects_unsupported_type(client: TestClient) -> None:
    response = client.post(
        "/api/ingest", files={"file": ("run.exe", b"MZ", "application/octet-stream")}
    )
    assert response.status_code == 422


def test_ingest_dedupes_existing_names(client: TestClient) -> None:
    first = client.post("/api/ingest", files={"file": ("a.md", b"# 1\n", "text/markdown")})
    second = client.post("/api/ingest", files={"file": ("a.md", b"# 2\n", "text/markdown")})
    assert first.json()["path"] == "00-Inbox/files/a.md"
    assert second.json()["path"] == "00-Inbox/files/a-2.md"


def test_ingest_survives_missing_rag_extras(vault: Path) -> None:
    app = create_app(
        Settings(_vault_path=vault), generator=fake_generator, index_factory=BrokenIndex
    )
    response = TestClient(app).post(
        "/api/ingest", files={"file": ("notes.md", b"# Notes\n", "text/markdown")}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["indexed"] is False and payload["chunks"] == 0
    assert (vault / "00-Inbox" / "files" / "notes.md").is_file(), "file saved despite no RAG"


def test_email_capture_archives_and_proposes(client: TestClient, vault: Path) -> None:
    response = client.post("/api/ingest/email", json={"text": EML_TEXT})
    assert response.status_code == 200
    payload = response.json()
    assert payload["proposals"] == 1
    assert payload["archived_path"].startswith("00-Inbox/emails/2026-07-09-")

    archived = (vault / payload["archived_path"]).read_text(encoding="utf-8")
    assert 'from: "Alice <alice@example.com>"' in archived
    assert 'subject: "Draft review before demo"' in archived
    assert "review the draft by 2026-07-20" in archived

    pending = client.get("/api/review").json()
    assert len(pending) == 1
    suggestion = pending[0]
    assert suggestion["kind"] == "note", "email extraction must be a proposal, not a write"
    assert suggestion["payload"]["path"] == payload["archived_path"]
    assert "email capture" in suggestion["rationale"]


def test_email_proposal_applies_through_writer(client: TestClient, vault: Path) -> None:
    archived_path = client.post("/api/ingest/email", json={"text": EML_TEXT}).json()[
        "archived_path"
    ]
    sid = client.get("/api/review").json()[0]["id"]

    approved = client.post(f"/api/review/{sid}/approve")
    assert approved.status_code == 200, approved.text

    content = (vault / archived_path).read_text(encoding="utf-8")
    assert "## Extracted (Argus proposal)" in content
    assert "- [ ] review the draft" in content
    assert "2026-07-20" in content


def test_email_capture_falls_back_without_agent(vault: Path) -> None:
    app = create_app(
        Settings(_vault_path=vault), generator=failing_generator, index_factory=FakeIndex
    )
    client = TestClient(app)
    response = client.post("/api/ingest/email", json={"text": EML_TEXT})
    assert response.status_code == 200
    assert response.json()["proposals"] == 1

    suggestion = client.get("/api/review").json()[0]
    assert "review the draft by 2026-07-20" in suggestion["payload"]["diff"], (
        "deterministic fallback must still find the bullet task"
    )


def test_email_capture_plain_paste_without_headers(client: TestClient, vault: Path) -> None:
    response = client.post(
        "/api/ingest/email", json={"text": "Reminder about the picnic\n\nBring snacks on Friday."}
    )
    assert response.status_code == 200
    archived = response.json()["archived_path"]
    assert archived.startswith("00-Inbox/emails/")
    assert "reminder-about-the-picnic" in archived
    assert (vault / archived).is_file()


def test_email_capture_rejects_empty_text(client: TestClient) -> None:
    assert client.post("/api/ingest/email", json={"text": "   "}).status_code == 422
