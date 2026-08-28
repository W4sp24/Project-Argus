"""DELETE /api/sources — removing a file from the vault and from the index.

These tests exist because the two halves of a delete were never checked
against each other. ``VaultIndex.delete_file`` had exactly one production
caller (``upsert_file``'s own delete-then-add step), so *nothing* dropped a
file's chunks when the file was deleted: the note was unlinked and chat went
on retrieving and citing it. A suite that asserts a 200 and an absent file
proves nothing about that, which is why every test below asserts the
agreement between components — file *and* chunks, guard *and* snapshot,
companion *and* the provenance it claims.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app


class FakeIndex:
    """A stand-in that records what was de-indexed.

    ``chunk_counts`` is how the real index reports per-path counts, and it is
    what the endpoint reads to answer ``chunks_removed`` truthfully.
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
        self.counts[rel_path] = 3
        return 3

    def size(self) -> dict[str, int]:
        return {"chunks": sum(self.counts.values()), "files": len(self.counts)}

    def schema_stale(self) -> bool:
        return False


class UnavailableIndex:
    """No ``[rag]`` extras. A delete must still delete."""

    def chunk_counts(self) -> dict[str, int]:
        raise ImportError("No module named 'chromadb'")

    def delete_file(self, rel_path: str) -> None:
        raise ImportError("No module named 'chromadb'")

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        raise ImportError("No module named 'chromadb'")


async def fake_generator(prompt: str, model: str | None = None) -> str:
    return "A generated summary."


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "15-Courses" / "CS301" / "materials").mkdir(parents=True)
    (root / "15-Courses" / "CS301" / "99-Private").mkdir(parents=True)
    (root / "99-Private").mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (root / "00-Inbox" / "files" / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / "00-Inbox" / "files" / "slides.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "99-Private" / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (root / "15-Courses" / "CS301" / "99-Private" / "exam.md").write_text("# E\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def index() -> FakeIndex:
    return FakeIndex(
        {
            "00-Inbox/files/a.md": 4,
            "00-Inbox/files/b.md": 2,
            "00-Inbox/files/slides.pdf": 7,
        }
    )


def _client(vault: Path, index: object) -> TestClient:
    app = create_app(
        Settings(_vault_path=vault),
        generator=fake_generator,
        index_factory=lambda: index,
        # Synchronous, so an ingest that seeds a generated note has finished
        # before the delete under test runs.
        ingest_job_runner=lambda run: run(),
    )
    return TestClient(app)


@pytest.fixture()
def client(vault: Path, index: FakeIndex) -> TestClient:
    return _client(vault, index)


def _delete(client: TestClient, paths: list[str], **body) -> object:
    # httpx's `.delete()` takes no body, and this route has one.
    return client.request("DELETE", "/api/sources", json={"paths": paths, **body})


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True
    ).stdout


def _snapshots(vault: Path) -> list[str]:
    return [
        line
        for line in _git(vault, "log", "--oneline").splitlines()
        if "argus: pre-apply snapshot" in line
    ]


# --- the file and its chunks go together -------------------------------------


def test_deleting_a_source_removes_the_file_and_its_chunks(
    client: TestClient, vault: Path, index: FakeIndex
) -> None:
    """The whole point. Unlinking alone left every chunk in the index, so a
    file deleted from the vault went on being retrieved and cited in chat —
    and a full reindex could not repair it, because reindex_all only walks
    files that still exist."""
    response = _delete(client, ["00-Inbox/files/a.md"])

    assert response.status_code == 200
    assert not (vault / "00-Inbox" / "files" / "a.md").exists()
    assert index.deleted == ["00-Inbox/files/a.md"]
    assert response.json() == {
        "files_removed": 1,
        "notes_removed": 0,
        "chunks_removed": 4,
        "removed": ["00-Inbox/files/a.md"],
    }


def test_a_batch_reports_the_chunks_it_actually_removed(
    client: TestClient, index: FakeIndex
) -> None:
    """Counts are read from the index before the delete, not guessed: `c.md`
    was never indexed and must contribute 0 rather than an invented number."""
    payload = _delete(
        client, ["00-Inbox/files/a.md", "00-Inbox/files/b.md", "00-Inbox/files/c.md"]
    ).json()

    assert payload["files_removed"] == 3
    assert payload["chunks_removed"] == 6
    assert index.deleted == [
        "00-Inbox/files/a.md",
        "00-Inbox/files/b.md",
        "00-Inbox/files/c.md",
    ]


def test_deleting_still_succeeds_when_the_index_is_unavailable(vault: Path) -> None:
    """The file being gone is the user's intent; a missing [rag] extra is not
    a reason to refuse it. Same posture /api/sources already takes."""
    response = _delete(_client(vault, UnavailableIndex()), ["00-Inbox/files/a.md"])

    assert response.status_code == 200
    assert response.json()["chunks_removed"] == 0
    assert not (vault / "00-Inbox" / "files" / "a.md").exists()


def test_a_non_markdown_source_is_deletable(client: TestClient, vault: Path) -> None:
    """`delete_note` is not markdown-specific despite the name — it is what
    lets a PDF be removed through the one write path (I1)."""
    payload = _delete(client, ["00-Inbox/files/slides.pdf"]).json()

    assert not (vault / "00-Inbox" / "files" / "slides.pdf").exists()
    assert payload["chunks_removed"] == 7


# --- guard-all, before anything is touched ------------------------------------


def test_a_private_path_anywhere_in_a_batch_refuses_the_whole_batch(
    client: TestClient, vault: Path, index: FakeIndex
) -> None:
    """All-or-nothing, the precedent `_validate_batch` sets for uploads: a
    half-applied delete leaves the user guessing which files survived."""
    response = _delete(
        client, ["00-Inbox/files/a.md", "99-Private/secret.md", "00-Inbox/files/b.md"]
    )

    assert response.status_code == 403
    assert "99-Private" in response.json()["detail"]
    assert (vault / "00-Inbox" / "files" / "a.md").is_file()
    assert (vault / "00-Inbox" / "files" / "b.md").is_file()
    assert (vault / "99-Private" / "secret.md").is_file()
    assert index.deleted == []
    assert _snapshots(vault) == [], "nothing was touched, so nothing was snapshotted"


def test_a_private_segment_below_the_top_level_is_refused(
    client: TestClient, vault: Path
) -> None:
    """`guard_user_path` judges parts[0] only, so 15-Courses/CS301/99-Private/x
    passes the *write* guard; `is_private_path` checks every segment. The two
    disagree, and this route satisfies both — /sources never lists such a file,
    but the API is reachable by more than the UI."""
    response = _delete(client, ["15-Courses/CS301/99-Private/exam.md"])

    assert response.status_code == 403
    assert (vault / "15-Courses" / "CS301" / "99-Private" / "exam.md").is_file()


def test_a_traversing_path_is_refused(client: TestClient) -> None:
    response = _delete(client, ["../escape.md"])

    assert response.status_code == 403


def test_an_absolute_windows_path_is_refused(client: TestClient) -> None:
    """`guard_user_path` judges Windows path semantics on every host, so this
    is refused on POSIX too — the rule is about the string the caller sent."""
    response = _delete(client, ["C:/Users/somebody/notes.md"])

    assert response.status_code == 403


def test_a_missing_path_is_a_404_and_takes_nothing_with_it(
    client: TestClient, vault: Path
) -> None:
    """Existence is part of the guard-all pass: a 404 raised mid-loop would
    arrive with the batch's earlier files already unlinked."""
    response = _delete(client, ["00-Inbox/files/a.md", "00-Inbox/files/ghost.md"])

    assert response.status_code == 404
    assert (vault / "00-Inbox" / "files" / "a.md").is_file()


def test_an_empty_batch_is_refused(client: TestClient) -> None:
    assert _delete(client, []).status_code == 422
    assert _delete(client, ["   "]).status_code == 422


# --- one snapshot, taken before the unlink ------------------------------------


def test_the_snapshot_commit_precedes_the_unlink(client: TestClient, vault: Path) -> None:
    """A snapshot taken *after* the unlink would commit the deletion and be no
    undo at all. The file must still be readable out of the commit that the
    delete left at HEAD."""
    _delete(client, ["00-Inbox/files/a.md"])

    assert not (vault / "00-Inbox" / "files" / "a.md").exists()
    assert "# a.md" in _git(vault, "show", "HEAD:00-Inbox/files/a.md")
    assert "argus: pre-apply snapshot (delete 1 source(s))" in _git(vault, "log", "--oneline")


def test_a_three_file_batch_takes_exactly_one_snapshot(
    client: TestClient, vault: Path
) -> None:
    """One per file is not merely wasteful: `_git_snapshot` runs git with
    check=False, so two snapshots racing on .git/index.lock lose one of them
    silently — I2 broken with nothing to show for it."""
    _delete(client, ["00-Inbox/files/a.md", "00-Inbox/files/b.md", "00-Inbox/files/c.md"])

    snapshots = _snapshots(vault)
    assert len(snapshots) == 1, snapshots
    assert "delete 3 source(s)" in snapshots[0]


def test_the_delete_leaves_one_audit_line_in_the_daily_note(
    client: TestClient, vault: Path
) -> None:
    """The file it names is gone, so this line and the snapshot commit are the
    only record the vault keeps of what happened."""
    _delete(client, ["00-Inbox/files/a.md", "00-Inbox/files/b.md"])

    daily = next((vault / "10-Daily").glob("*.md"))
    body = daily.read_text(encoding="utf-8")
    assert body.count("deleted 2 file(s)") == 1
    assert "00-Inbox/files/a.md" in body


# --- the generated companion note ---------------------------------------------


def _seed_generated_note(client: TestClient, name: str, target: str) -> None:
    """Ingest one file so the *real* note writer produces its companion."""
    response = client.post(
        "/api/ingest/jobs",
        files=[("files", (name, b"# Lecture\n\nContent.\n", "text/markdown"))],
        data={"target": target, "summary_prompt": "list the key definitions"},
    )
    assert response.status_code == 202


def test_include_generated_removes_the_companion_that_claims_the_source(
    client: TestClient, vault: Path, index: FakeIndex
) -> None:
    """Resolved from the note's own frontmatter `source`, written by the note
    writer — not from a filename heuristic. The candidate comes from the same
    note_destination() rule that wrote it, and the frontmatter has to agree."""
    _seed_generated_note(client, "lecture.md", "00-Inbox/files")
    companion = vault / "00-Inbox" / "files" / "lecture.summary.md"
    assert companion.is_file(), "the ingest under test must have written one"

    payload = _delete(client, ["00-Inbox/files/lecture.md"], include_generated=True).json()

    assert not companion.exists()
    assert payload["files_removed"] == 1
    assert payload["notes_removed"] == 1, "the companion is a note, not a file"
    assert payload["removed"] == ["00-Inbox/files/lecture.md", "00-Inbox/files/lecture.summary.md"]
    assert "00-Inbox/files/lecture.summary.md" in index.deleted


def test_include_generated_finds_a_companion_in_a_course_notes_zone(
    client: TestClient, vault: Path
) -> None:
    """A course material's note is written to <course>/notes/, not beside the
    file. One rule computes both, so the delete follows the writer."""
    _seed_generated_note(client, "wk1.md", "15-Courses/CS301/materials")
    companion = vault / "15-Courses" / "CS301" / "notes" / "wk1.notes.md"
    assert companion.is_file()

    _delete(client, ["15-Courses/CS301/materials/wk1.md"], include_generated=True)

    assert not companion.exists()


def test_a_note_that_claims_another_source_is_left_alone(
    client: TestClient, vault: Path
) -> None:
    """The provenance check is the whole safety of this feature: a file that
    merely sits at the companion's name is somebody else's note."""
    impostor = vault / "00-Inbox" / "files" / "a.summary.md"
    impostor.write_text(
        "---\ntitle: mine\nsource: 00-Inbox/files/something-else.md\n---\n\nMy own notes.\n",
        encoding="utf-8",
    )

    payload = _delete(client, ["00-Inbox/files/a.md"], include_generated=True).json()

    assert impostor.is_file(), "a note that does not claim this source is not ours to delete"
    assert payload["notes_removed"] == 0


def test_a_note_with_no_source_frontmatter_is_left_alone(
    client: TestClient, vault: Path
) -> None:
    """Hand-written notes named `<stem>.summary.md` predate the convention and
    carry no `source:` at all."""
    handwritten = vault / "00-Inbox" / "files" / "b.summary.md"
    handwritten.write_text("# My summary\n", encoding="utf-8")

    _delete(client, ["00-Inbox/files/b.md"], include_generated=True)

    assert handwritten.is_file()


def test_the_companion_is_kept_when_include_generated_is_off(
    client: TestClient, vault: Path
) -> None:
    """Off by default: a user re-uploading a corrected PDF may well want to
    keep the notes they have since annotated."""
    _seed_generated_note(client, "lecture.md", "00-Inbox/files")

    payload = _delete(client, ["00-Inbox/files/lecture.md"]).json()

    assert (vault / "00-Inbox" / "files" / "lecture.summary.md").is_file()
    assert payload["notes_removed"] == 0


def test_deleting_a_generated_note_directly_looks_for_no_companion_of_its_own(
    client: TestClient, vault: Path
) -> None:
    """`lecture.summary.md` would otherwise resolve a companion named
    `lecture.summary.summary.md`. `generated_kind()` is the one definition of
    that convention, so this asks it rather than growing a fourth copy."""
    _seed_generated_note(client, "lecture.md", "00-Inbox/files")

    payload = _delete(
        client, ["00-Inbox/files/lecture.summary.md"], include_generated=True
    ).json()

    assert not (vault / "00-Inbox" / "files" / "lecture.summary.md").exists()
    assert (vault / "00-Inbox" / "files" / "lecture.md").is_file()
    assert payload["notes_removed"] == 0
    assert payload["files_removed"] == 1


def test_a_batch_naming_both_a_source_and_its_companion_deletes_each_once(
    client: TestClient, vault: Path, index: FakeIndex
) -> None:
    """Both halves resolve to the same path; a second unlink of it would raise
    WriterMissing and 404 a request that had already succeeded."""
    _seed_generated_note(client, "lecture.md", "00-Inbox/files")

    payload = _delete(
        client,
        ["00-Inbox/files/lecture.md", "00-Inbox/files/lecture.summary.md"],
        include_generated=True,
    ).json()

    assert payload["removed"] == [
        "00-Inbox/files/lecture.md",
        "00-Inbox/files/lecture.summary.md",
    ]
    assert index.deleted.count("00-Inbox/files/lecture.summary.md") == 1
