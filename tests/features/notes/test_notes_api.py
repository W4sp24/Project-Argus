"""Tests for note + task-line CRUD endpoints (thin HTTP layer over writer)."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app


class FakeIndex:
    """Records what a route de-indexes, and reports counts like the real one.

    Injected rather than left to the default factory so these tests never
    construct a real chroma client — and so the delete route's de-index is
    observable at all.
    """

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts: dict[str, int] = dict(counts or {})
        self.deleted: list[str] = []

    def chunk_counts(self) -> dict[str, int]:
        return dict(self.counts)

    def delete_file(self, rel_path: str) -> None:
        self.deleted.append(rel_path)
        self.counts.pop(rel_path, None)

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        return 0


@pytest.fixture()
def index() -> FakeIndex:
    return FakeIndex({"00-Inbox/note.md": 5})


@pytest.fixture()
def client(tmp_path: Path, index: FakeIndex) -> tuple[TestClient, Path]:
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
    app = create_app(settings, chat_runner=lambda m: iter(()), index_factory=lambda: index)
    return TestClient(app), vault


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


def test_deleting_a_note_removes_its_chunks_too(client, index):
    """The unlink was the only half this route ever did.

    ``VaultIndex.delete_file``'s one production caller was ``upsert_file``'s
    internal delete-then-add, so nothing dropped a deleted note's chunks:
    search and chat went on retrieving and citing a note that no longer
    existed, and a full ``reindex_all`` could not repair it — it walks the
    files that exist, so a path that is gone is never visited.
    """
    api, vault = client

    response = api.request("DELETE", "/api/note", params={"path": "00-Inbox/note.md"})

    assert not (vault / "00-Inbox" / "note.md").exists()
    assert index.deleted == ["00-Inbox/note.md"]
    assert response.json()["chunks_removed"] == 5


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


# --- GET /api/notes: folder filter + frontmatter whitelist (research vault) --


def test_notes_default_response_is_unchanged(client):
    """Regression guard: the folder/fields params must be purely additive.

    No caller of ``GET /api/notes`` passes them today (``useNotes()`` and
    friends), so the default response must be byte-identical to before this
    endpoint gained them — in particular, no stray ``frontmatter`` key.
    """
    api, _ = client
    response = api.get("/api/notes")
    assert response.status_code == 200
    payload = response.json()
    by_path = {note["path"]: note for note in payload}
    assert set(by_path) == {"20-Projects/p.md", "00-Inbox/note.md"}
    assert by_path["00-Inbox/note.md"]["title"] == "note"
    assert by_path["00-Inbox/note.md"]["folder"] == "00-Inbox"
    for note in payload:
        assert "frontmatter" not in note, "frontmatter must be excluded, not null, by default"
        assert set(note.keys()) == {"path", "title", "folder", "modified"}


def test_notes_folder_filters_to_subtree(client):
    api, vault = client
    (vault / "30-Areas" / "papers").mkdir(parents=True)
    (vault / "30-Areas" / "papers" / "a.md").write_text(
        "---\ntype: paper\n---\n# A\n", encoding="utf-8"
    )
    (vault / "30-Areas" / "other.md").write_text("# not a paper\n", encoding="utf-8")

    response = api.get("/api/notes", params={"folder": "30-Areas/papers"})
    assert response.status_code == 200
    paths = {note["path"] for note in response.json()}
    assert paths == {"30-Areas/papers/a.md"}


def test_notes_fields_whitelists_frontmatter(client):
    api, vault = client
    (vault / "30-Areas" / "papers").mkdir(parents=True)
    (vault / "30-Areas" / "papers" / "a.md").write_text(
        '---\ntype: paper\nstatus: reading\nprogress: 40\nsecret: nope\n---\n# A\n',
        encoding="utf-8",
    )

    response = api.get(
        "/api/notes",
        params={"folder": "30-Areas/papers", "fields": "type,status,progress"},
    )
    assert response.status_code == 200
    [note] = response.json()
    assert note["frontmatter"] == {"type": "paper", "status": "reading", "progress": 40}
    assert "secret" not in note["frontmatter"]


def test_notes_fields_omitted_key_yields_no_frontmatter_entry(client):
    """A note missing a requested key just doesn't carry that key — no error,
    no null placeholder."""
    api, vault = client
    (vault / "30-Areas" / "papers").mkdir(parents=True)
    (vault / "30-Areas" / "papers" / "a.md").write_text(
        "---\ntype: paper\n---\n# A\n", encoding="utf-8"
    )

    response = api.get(
        "/api/notes",
        params={"folder": "30-Areas/papers", "fields": "type,status"},
    )
    [note] = response.json()
    assert note["frontmatter"] == {"type": "paper"}
