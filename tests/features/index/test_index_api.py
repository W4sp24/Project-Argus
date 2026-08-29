"""Tests for POST /api/index/reindex and GET /api/index/status.

Uses a fake VaultIndex (never the real one — that would load the embedding
model and make this suite slow) that records calls so the tests can assert on
threading behavior: one rebuild in flight at a time, status reflecting it.

Since the fold onto the shared job store these also pin the *shape* of
``IndexStatus`` across that move, and the cross-feature single-flight it
introduced (an ingest and a reindex used to be able to run at once).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from backend.rag.index import ReindexResult

# The reset fixture that used to live here is gone with the module-global
# `_State` it reset. Reindex status is a projection over the job store now, so
# each test's own `tmp_path` vault -- and therefore its own database -- is the
# clean slate; nothing leaks between tests through module scope any more.


class FakeIndex:
    """Records calls; ``reindex_all`` can be told to error, or to take a
    moment so a test can observe "still indexing" before it finishes."""

    call_count = 0
    _class_lock = threading.Lock()

    def __init__(
        self,
        *,
        chunks: int = 5,
        files: int = 2,
        stale: bool = False,
        delay: float = 0.0,
        error: str | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.chunks = chunks
        self.files = files
        self._stale = stale
        self.delay = delay
        self.error = error
        #: Per-file failures, as `ReindexResult.errors` reports them.
        self.errors = errors or {}
        self.upserts: list[str] = []

    def size(self) -> dict[str, int]:
        return {"chunks": self.chunks, "files": self.files}

    def schema_stale(self) -> bool:
        return self._stale

    def reindex_all(self, vault_path: Path) -> ReindexResult:
        with FakeIndex._class_lock:
            FakeIndex.call_count += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return ReindexResult(counts={"note.md": self.chunks}, errors=dict(self.errors))

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        """Records what a scoped reindex actually touched.

        Also the de-index path: the real one deletes a file's chunks before
        adding any, and returns 0 for a file that is no longer on disk.
        """
        self.upserts.append(rel_path)
        if rel_path in self.errors:
            raise RuntimeError(self.errors[rel_path])
        return self.chunks


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def _client(vault: Path, fake: FakeIndex) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault), index_factory=lambda: fake))


def _sync_client(vault: Path, fake: FakeIndex, **kwargs) -> TestClient:
    """A client whose jobs run to completion inside the POST that starts them.

    The reindex route is a synchronous `def`, so FastAPI already runs it off
    the event loop and the job body can bridge to async exactly as it does on
    its daemon thread. Nothing here is testing `threading`.
    """
    return TestClient(
        create_app(
            Settings(_vault_path=vault),
            index_factory=lambda: fake,
            ingest_job_runner=lambda run: run(),
            **kwargs,
        )
    )


def _wait_until_not_indexing(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    payload = client.get("/api/index/status").json()
    while payload["indexing"] and time.monotonic() < deadline:
        time.sleep(0.02)
        payload = client.get("/api/index/status").json()
    return payload


def test_reindex_returns_202(vault: Path) -> None:
    FakeIndex.call_count = 0
    client = _client(vault, FakeIndex())

    response = client.post("/api/index/reindex")

    assert response.status_code == 202
    _wait_until_not_indexing(client)


def test_reindex_actually_runs_and_status_reports_counts(vault: Path) -> None:
    FakeIndex.call_count = 0
    fake = FakeIndex(chunks=7, files=3)
    client = _client(vault, fake)

    client.post("/api/index/reindex")
    payload = _wait_until_not_indexing(client)

    assert payload["chunks"] == 7
    assert payload["files"] == 3
    assert payload["indexing"] is False
    assert payload["last_error"] is None
    assert payload["last_run"] is not None
    assert FakeIndex.call_count == 1


def test_concurrent_reindex_calls_start_only_one_rebuild(vault: Path) -> None:
    FakeIndex.call_count = 0
    fake = FakeIndex(delay=0.3)
    client = _client(vault, fake)

    first = client.post("/api/index/reindex")
    assert first.json()["indexing"] is True

    # Fired while the first rebuild is still (deliberately) running.
    second = client.post("/api/index/reindex")
    assert second.status_code == 202
    assert second.json()["indexing"] is True

    _wait_until_not_indexing(client)
    assert FakeIndex.call_count == 1, "a second overlapping POST must not start another rebuild"


def test_status_reports_last_error_on_failure(vault: Path) -> None:
    FakeIndex.call_count = 0
    fake = FakeIndex(error="chroma directory is corrupt")
    client = _client(vault, fake)

    client.post("/api/index/reindex")
    payload = _wait_until_not_indexing(client)

    assert payload["indexing"] is False
    assert "chroma directory is corrupt" in payload["last_error"]


def test_status_before_any_reindex_reports_current_index_size(vault: Path) -> None:
    fake = FakeIndex(chunks=11, files=4)
    client = _client(vault, fake)

    payload = client.get("/api/index/status").json()

    assert payload == {
        "chunks": 11,
        "files": 4,
        "indexing": False,
        "last_run": None,
        "last_error": None,
        "stale": False,
    }


def test_status_reports_stale_schema(vault: Path) -> None:
    fake = FakeIndex(stale=True)
    client = _client(vault, fake)

    payload = client.get("/api/index/status").json()

    assert payload["stale"] is True


def test_status_degrades_gracefully_when_index_is_unavailable(vault: Path) -> None:
    class BrokenIndex:
        def size(self):
            raise ImportError("No module named 'chromadb'")

        def schema_stale(self):
            raise ImportError("No module named 'chromadb'")

    client = TestClient(
        create_app(Settings(_vault_path=vault), index_factory=lambda: BrokenIndex())
    )

    response = client.get("/api/index/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chunks"] == 0
    assert payload["files"] == 0


# --- the status shape survives the move onto the job store ---------------------


def test_index_status_keeps_exactly_the_fields_it_always_had(vault: Path) -> None:
    """`IndexStatus` is a projection over an `ingest_jobs` row now, not a copy
    of a module-global object. The projection is only worth anything if the
    wire shape did not move with it: `web/lib/api.ts` declares these six keys
    and reads them positionally by name, so an extra key or a renamed one
    would be a silent frontend break in a change that never touched `web/`.
    """
    fake = FakeIndex(chunks=11, files=4)
    client = _sync_client(vault, fake)

    before = client.get("/api/index/status").json()
    client.post("/api/index/reindex")
    after = client.get("/api/index/status").json()

    expected = {"chunks", "files", "indexing", "last_run", "last_error", "stale"}
    assert set(before) == expected
    assert set(after) == expected
    assert before["last_run"] is None and before["last_error"] is None
    assert after["indexing"] is False
    assert after["last_run"] is not None, "a finished run must report when it finished"
    assert after["last_error"] is None


def test_a_finished_reindex_leaves_a_job_with_one_item_per_file(vault: Path) -> None:
    """`ReindexResult.counts` used to be discarded outright and `errors` joined
    into a single string, so "which files failed, and how many chunks did the
    rest produce?" had no answer anywhere. They are `ingest_job_items` now."""
    fake = FakeIndex(chunks=7, errors={"broken.pdf": "extraction failed"})
    client = _sync_client(vault, fake)

    client.post("/api/index/reindex")

    jobs = client.get("/api/ingest/jobs", params={"kind": "reindex"}).json()["jobs"]
    assert len(jobs) == 1
    job = client.get(f"/api/ingest/jobs/{jobs[0]['id']}").json()
    assert job["kind"] == "reindex"
    # One file indexed, one failed -- neither outcome erases the other.
    assert job["status"] == "partial"
    by_name = {item["filename"]: item for item in job["items"]}
    assert by_name["note.md"]["stage"] == "done"
    assert by_name["note.md"]["chunks"] == 7
    assert by_name["broken.pdf"]["stage"] == "failed"
    assert by_name["broken.pdf"]["failed_stage"] == "indexing"
    # The joined string `last_error` always showed is still exactly that.
    assert client.get("/api/index/status").json()["last_error"] == (
        "broken.pdf: extraction failed"
    )


def test_a_reindex_history_does_not_leak_into_the_ingest_history(vault: Path) -> None:
    """Both are rows in the same table now. The ingest panel asks this route
    for ingest history and must keep getting only that."""
    client = _sync_client(vault, FakeIndex())

    client.post("/api/index/reindex")

    assert client.get("/api/ingest/jobs").json()["jobs"] == []
    assert len(client.get("/api/ingest/jobs", params={"kind": "all"}).json()["jobs"]) == 1


# --- path-scoped reindex -------------------------------------------------------


def test_a_scoped_reindex_touches_only_the_named_paths(vault: Path) -> None:
    """What a future "index these files" button calls. `upsert_file` deletes a
    file's chunks before adding new ones and returns 0 for a file that is no
    longer there, so the same request also de-indexes a deleted file --
    without it, the only way to correct one file was to rebuild the vault."""
    FakeIndex.call_count = 0
    fake = FakeIndex(chunks=4)
    client = _sync_client(vault, fake)

    response = client.post(
        "/api/index/reindex", json={"paths": ["notes/a.md", "notes/b.md"]}
    )

    assert response.status_code == 202
    assert fake.upserts == ["notes/a.md", "notes/b.md"]
    assert FakeIndex.call_count == 0, "a scoped reindex must never walk the whole vault"


def test_a_scoped_reindex_reports_per_file_progress(vault: Path) -> None:
    """Unlike the full rebuild this knows its files up front, so the rows exist
    before the work starts rather than appearing all at once at the end."""
    fake = FakeIndex(chunks=4, errors={"notes/bad.md": "unreadable"})
    client = _sync_client(vault, fake)

    client.post("/api/index/reindex", json={"paths": ["notes/a.md", "notes/bad.md"]})
    jobs = client.get("/api/ingest/jobs", params={"kind": "reindex"}).json()["jobs"]
    job_id = jobs[0]["id"]

    job = client.get(f"/api/ingest/jobs/{job_id}").json()
    assert job["params"] == {"paths": ["notes/a.md", "notes/bad.md"]}
    assert job["total"] == 2
    by_name = {item["filename"]: item for item in job["items"]}
    assert by_name["notes/a.md"]["stage"] == "done"
    assert by_name["notes/a.md"]["chunks"] == 4
    assert by_name["notes/bad.md"]["stage"] == "failed"
    assert "unreadable" in by_name["notes/bad.md"]["error"]


def test_a_scoped_reindex_refuses_a_path_that_escapes_the_vault(vault: Path) -> None:
    """`VaultIndex.upsert_file` gates on `is_indexable`, which checks the
    suffix and the excluded top directories and says nothing about `..`. An
    unguarded list would let a caller name a file outside the vault and have
    its contents embedded into the collection every chat answer is retrieved
    from."""
    fake = FakeIndex()
    client = _sync_client(vault, fake)

    response = client.post("/api/index/reindex", json={"paths": ["../../secrets.md"]})

    assert response.status_code == 400
    assert fake.upserts == []


def test_a_scoped_reindex_refuses_a_protected_zone(vault: Path) -> None:
    """I3: `99-Private/` must never enter the index, whichever route asks."""
    fake = FakeIndex()
    client = _sync_client(vault, fake)

    response = client.post("/api/index/reindex", json={"paths": ["99-Private/diary.md"]})

    assert response.status_code == 400
    assert fake.upserts == []


def test_an_empty_path_list_is_refused_rather_than_widened(vault: Path) -> None:
    """The caller asked for a scoped reindex and named nothing; walking the
    whole vault instead is the opposite of what was asked. Same reasoning as
    `sources: []` in the study routes."""
    FakeIndex.call_count = 0
    fake = FakeIndex()
    client = _sync_client(vault, fake)

    response = client.post("/api/index/reindex", json={"paths": []})

    assert response.status_code == 422
    assert FakeIndex.call_count == 0


# --- an ingest and a reindex are one at a time --------------------------------


def test_a_reindex_is_refused_while_an_ingest_is_running(vault: Path) -> None:
    """The behaviour change. These held two independent single-flight locks
    before the job models were folded together, so an ingest and a reindex
    could run at the same time -- two copies of the bge-small embedding model
    loaded, both writing the same chroma directory, and two overlapping
    `git add -A` runs racing on `.git/index.lock` that `_git_snapshot`, which
    runs git with `check=False`, would not even report losing."""
    FakeIndex.call_count = 0
    fake = FakeIndex()
    started: list[str] = []
    client = TestClient(
        create_app(
            Settings(_vault_path=vault),
            index_factory=lambda: fake,
            # Never actually runs, so the ingest stays in flight.
            ingest_job_runner=lambda run: started.append("scheduled"),
        )
    )
    accepted = client.post(
        "/api/ingest/jobs",
        files=[("files", ("a.md", b"# A\n", "text/markdown"))],
        data={"target": "00-Inbox/files"},
    )
    assert accepted.status_code == 202

    response = client.post("/api/index/reindex")

    assert response.status_code == 409
    assert "ingest" in response.json()["detail"]
    assert FakeIndex.call_count == 0


def test_last_run_keeps_the_iso_format_it_has_always_published(vault: Path) -> None:
    """Folding reindex into the job table moved the timestamp's storage into
    SQLite, whose `datetime('now')` is `YYYY-MM-DD HH:MM:SS` with no zone.
    `last_run` is a published field with a published shape, and nothing in the
    frontend renders it today -- which is exactly why a silent format change
    here would surface much later, somewhere else, as an Invalid Date."""
    FakeIndex.call_count = 0
    client = _client(vault, FakeIndex(chunks=1, files=1))
    client.post("/api/index/reindex")

    stamp = _wait_until_not_indexing(client)["last_run"]

    assert stamp is not None
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None, "a bare local-looking stamp is the regression"
