"""Tests for GET /api/search (fake index — no chromadb/embedding deps needed)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app

CHUNKS = [
    {
        "text": "Dijkstra's algorithm finds shortest paths using a priority queue.",
        "meta": {"path": "50-Reference/algorithms.md", "title": "Algorithms", "wikilinks": ""},
    },
    {
        "text": "Bought groceries and studied Dijkstra today.",
        "meta": {"path": "10-Daily/2026-07-11.md", "title": "2026-07-11", "wikilinks": ""},
    },
]


class FakeIndex:
    """Enough of VaultIndex's surface for backend.rag.retrieve.retrieve."""

    def query(self, text: str, n_results: int = 20, where: dict | None = None) -> list[dict]:
        return [{**chunk, "score": 0.9} for chunk in CHUNKS]

    def all_chunks(self) -> list[dict]:
        return [dict(chunk) for chunk in CHUNKS]


class BrokenIndex:
    """Simulates a missing/unbuilt index — e.g. [rag] extras not installed."""

    def query(self, text: str, n_results: int = 20, where: dict | None = None) -> list[dict]:
        raise ImportError("No module named 'chromadb'")

    def all_chunks(self) -> list[dict]:
        raise ImportError("No module named 'chromadb'")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


def _client(vault: Path, index_factory) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault), index_factory=index_factory))


def test_search_returns_cited_snippets(vault: Path) -> None:
    client = _client(vault, lambda: FakeIndex())

    response = client.get("/api/search", params={"q": "shortest paths"})

    assert response.status_code == 200
    results = response.json()
    assert results, "expected at least one result"
    assert results[0]["source_path"] == "50-Reference/algorithms.md"
    assert "Dijkstra" in results[0]["snippet"]
    assert isinstance(results[0]["score"], float)


def test_empty_query_returns_empty_list_without_crashing(vault: Path) -> None:
    client = _client(vault, lambda: FakeIndex())

    response = client.get("/api/search", params={"q": "   "})
    assert response.status_code == 200
    assert response.json() == []

    response_missing = client.get("/api/search")
    assert response_missing.status_code == 200
    assert response_missing.json() == []


def test_unbuilt_index_degrades_to_empty_list(vault: Path) -> None:
    client = _client(vault, lambda: BrokenIndex())

    response = client.get("/api/search", params={"q": "anything"})

    assert response.status_code == 200
    assert response.json() == []
