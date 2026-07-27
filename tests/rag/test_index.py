"""Tests for the vector index and hybrid retrieval (needs the [rag] extra)."""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

HAS_RAG = all(
    importlib.util.find_spec(module) is not None
    for module in ("chromadb", "sentence_transformers", "rank_bm25")
)
pytestmark = pytest.mark.skipif(not HAS_RAG, reason="rag extra not installed")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "50-Reference").mkdir(parents=True)
    (root / "10-Daily").mkdir()
    (root / "99-Private").mkdir()

    (root / "50-Reference" / "algorithms.md").write_text(
        "---\ntitle: Algorithms\ntags: [cs]\n---\n\n# Algorithms\n\n"
        "Dijkstra's algorithm finds shortest paths using a priority queue. See [[Mom]].\n",
        encoding="utf-8",
    )
    (root / "10-Daily" / "2026-07-11.md").write_text(
        "---\ntags: [daily]\n---\n\nBought groceries and studied Dijkstra today.\n",
        encoding="utf-8",
    )
    (root / "10-Daily" / "2025-01-01.md").write_text(
        "---\ntags: [daily]\n---\n\nOld note also about Dijkstra from long ago.\n",
        encoding="utf-8",
    )
    (root / "99-Private" / "diary.md").write_text(
        "# Diary\n\nDijkstra secret private thoughts.\n", encoding="utf-8"
    )
    (root / "Mom.md").write_text("Mom's birthday is in March.\n", encoding="utf-8")
    return root


@pytest.fixture()
def index(vault: Path, tmp_path: Path):
    from backend.rag.index import VaultIndex

    vault_index = VaultIndex(tmp_path / "chroma")
    vault_index.reindex_all(vault)
    return vault_index


def test_private_notes_never_indexed(index) -> None:
    stored_paths = {chunk["meta"]["path"] for chunk in index.all_chunks()}
    assert stored_paths, "index is empty"
    assert not any(path.startswith("99-Private") for path in stored_paths), "I3 violation"


def test_retrieval_returns_seeded_fact_with_citation_meta(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve

    hits = retrieve(index, "how do I find shortest paths?", vault, k=4)

    assert hits, "no results"
    top_paths = [hit["meta"]["path"] for hit in hits]
    assert "50-Reference/algorithms.md" in top_paths


def test_recency_boost_prefers_fresh_daily_notes(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve

    hits = retrieve(
        index, "Dijkstra daily study", vault, k=8, expand_links=False, today=date(2026, 7, 12)
    )
    daily_paths = [
        hit["meta"]["path"] for hit in hits if hit["meta"]["path"].startswith("10-Daily")
    ]

    assert daily_paths, "no daily notes retrieved"
    assert daily_paths[0] == "10-Daily/2026-07-11.md", "fresh daily note must outrank stale one"


def test_wikilink_expansion_pulls_linked_note(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve

    hits = retrieve(index, "shortest paths priority queue", vault, k=4)
    linked = [hit for hit in hits if hit["meta"].get("linked")]

    assert any(hit["meta"]["path"] == "Mom.md" for hit in linked)


def test_reindex_is_idempotent(index, vault: Path) -> None:
    before = len(index.all_chunks())
    index.reindex_all(vault)
    assert len(index.all_chunks()) == before
