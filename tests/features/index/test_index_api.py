"""Tests for POST /api/index/reindex and GET /api/index/status.

Uses a fake VaultIndex (never the real one — that would load the embedding
model and make this suite slow) that records calls so the tests can assert on
threading behavior: one rebuild in flight at a time, status reflecting it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.features.index.router as index_router
from backend.core.config import Settings
from backend.main import create_app
from backend.rag.index import ReindexResult


@pytest.fixture(autouse=True)
def _reset_index_router_state():
    """The router's reindex lock/status are module-level (by design — see the
    router's docstring), so each test must start from a clean slate."""
    index_router._state = index_router._State()
    yield
    index_router._state = index_router._State()


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
    ) -> None:
        self.chunks = chunks
        self.files = files
        self._stale = stale
        self.delay = delay
        self.error = error

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
        return ReindexResult(counts={"note.md": self.chunks})


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def _client(vault: Path, fake: FakeIndex) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault), index_factory=lambda: fake))


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
