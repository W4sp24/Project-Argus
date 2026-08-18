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


def test_reindex_returns_result_with_counts_and_no_errors(vault: Path, tmp_path: Path) -> None:
    from backend.rag.index import VaultIndex

    fresh = VaultIndex(tmp_path / "chroma2")
    result = fresh.reindex_all(vault)

    assert result.errors == {}
    assert result.files == len(result.counts)
    assert result.total_chunks == sum(result.counts.values())
    assert result.total_chunks > 0
    assert any(path.startswith("50-Reference") for path in result.counts)


def test_reindex_reports_unreadable_file_without_aborting(vault: Path, tmp_path: Path) -> None:
    """A single unreadable file must be reported, not silently dropped."""
    from backend.rag.index import VaultIndex

    bad_pdf = vault / "50-Reference" / "broken.pdf"
    bad_pdf.write_bytes(b"not actually a pdf")

    fresh = VaultIndex(tmp_path / "chroma3")
    result = fresh.reindex_all(vault)

    assert "50-Reference/broken.pdf" in result.errors
    # The rest of the vault must still have been indexed.
    assert any(path.startswith("50-Reference/algorithms.md") for path in result.counts)


def test_size_reports_chunks_and_files_without_loading_embedding_model(
    index, tmp_path: Path
) -> None:
    from backend.rag.index import VaultIndex

    # A brand-new VaultIndex object pointed at the same (already-populated)
    # chroma directory: its own `_model` has never been touched, so if size()
    # loaded the embedding model, this would catch it. Using `index` itself
    # would prove nothing — its fixture already indexed the vault, which
    # necessarily loaded the model long before this test body ran.
    fresh = VaultIndex(tmp_path / "chroma")
    size = fresh.size()

    assert size["chunks"] > 0
    assert size["files"] > 0
    assert fresh._model is None


def test_size_on_empty_index_is_zero(tmp_path: Path) -> None:
    from backend.rag.index import VaultIndex

    empty = VaultIndex(tmp_path / "chroma-empty")
    assert empty.size() == {"chunks": 0, "files": 0}


# --- schema marker -----------------------------------------------------------


def test_fresh_index_is_not_stale(tmp_path: Path) -> None:
    from backend.rag.index import VaultIndex

    fresh = VaultIndex(tmp_path / "chroma")
    assert fresh.schema_stale() is False


def test_absent_schema_metadata_is_treated_as_stale(tmp_path: Path) -> None:
    """A v0.2.0 index has no schema_version key at all — that must count as stale."""
    import chromadb

    from backend.rag.index import VaultIndex

    db_dir = tmp_path / "chroma"
    # Simulate a pre-existing v0.2.0 collection: created with no schema marker.
    client = chromadb.PersistentClient(path=str(db_dir))
    client.get_or_create_collection("vault", metadata={"hnsw:space": "cosine"})

    legacy = VaultIndex(db_dir)
    assert legacy.schema_stale() is True


def test_schema_version_1_index_is_stale_after_the_bump(tmp_path: Path) -> None:
    """A pre-bump (SCHEMA_VERSION 1) index must be treated as stale too, not
    just a totally absent marker -- old-shape chunks (run-on text, no
    heading, no inline tags) must never silently coexist with new ones."""
    import chromadb

    from backend.rag.index import VaultIndex

    db_dir = tmp_path / "chroma"
    client = chromadb.PersistentClient(path=str(db_dir))
    client.get_or_create_collection("vault", metadata={"hnsw:space": "cosine", "schema_version": 1})

    legacy = VaultIndex(db_dir)
    assert legacy.schema_stale() is True


def test_reindex_recreates_a_stale_collection_and_keeps_cosine_space(
    vault: Path, tmp_path: Path
) -> None:
    """get_or_create_collection does not update metadata on an existing
    collection, so reindex_all must explicitly drop + recreate a stale one —
    and must not lose the cosine distance setting while doing it."""
    import chromadb

    from backend.rag.index import VaultIndex

    db_dir = tmp_path / "chroma"
    client = chromadb.PersistentClient(path=str(db_dir))
    stale = client.get_or_create_collection("vault", metadata={"hnsw:space": "cosine"})
    # Leave behind a chunk shaped like the old schema (no "path" metadata key)
    # to prove the rebuild doesn't just try to delete-by-path over it.
    stale.add(ids=["legacy-1"], documents=["leftover text"], embeddings=[[0.0] * 384])

    legacy_index = VaultIndex(db_dir)
    assert legacy_index.schema_stale() is True

    result = legacy_index.reindex_all(vault)

    assert legacy_index.schema_stale() is False
    assert legacy_index.collection.metadata["hnsw:space"] == "cosine"
    # The leftover legacy-shape chunk must be gone after the rebuild.
    all_ids = legacy_index.collection.get(include=[])["ids"]
    assert "legacy-1" not in all_ids
    assert result.total_chunks > 0


# --- corpus / BM25 caching ---------------------------------------------------
# retrieve() used to call index.all_chunks() on every query, and all_chunks()
# does a full collection.get() of the whole vault -- O(whole vault) per search.


def test_all_chunks_and_bm25_are_built_once_for_repeated_queries(index, monkeypatch) -> None:
    calls = {"get": 0}
    real_get = index.collection.get

    def counting_get(*args, **kwargs):
        calls["get"] += 1
        return real_get(*args, **kwargs)

    monkeypatch.setattr(index.collection, "get", counting_get)

    first = index.all_chunks()
    bm25_first = index.bm25()
    for _ in range(5):
        assert index.all_chunks() == first
        assert index.bm25() is bm25_first

    assert calls["get"] == 1, f"corpus re-fetched {calls['get']} times across 6 calls"


def test_a_mutation_invalidates_the_cached_corpus(index, vault: Path) -> None:
    before = index.all_chunks()
    bm25_before = index.bm25()

    (vault / "50-Reference" / "extra.md").write_text(
        "---\ntitle: Extra\n---\n\nA brand new note about Kruskal.\n", encoding="utf-8"
    )
    index.upsert_file(vault, "50-Reference/extra.md")

    after = index.all_chunks()
    assert len(after) > len(before), "new chunks not visible after upsert"
    assert index.bm25() is not bm25_before, "stale BM25 survived an index mutation"


def test_delete_also_invalidates_the_cached_corpus(index) -> None:
    before = index.all_chunks()
    index.delete_file("50-Reference/algorithms.md")
    after = index.all_chunks()
    assert len(after) < len(before), "deleted chunks still served from cache"


# --- link index caching -------------------------------------------------------
# build_link_index() rglob()s the whole vault and parses every note's YAML
# frontmatter -- retrieve() used to pay that cost on every single call with
# expand_links=True, wikilinks or not.


def test_link_index_is_built_once_across_repeated_queries_and_invalidates_on_upsert(
    index, vault: Path, monkeypatch
) -> None:
    """``index.link_index()`` itself is expected to be called on every
    retrieve() -- what must happen only once (until a mutation) is the
    expensive walk inside ``build_link_index``, which is what's counted here."""
    from backend.rag import index as index_module
    from backend.rag.retrieve import retrieve

    calls = {"n": 0}
    real_build = index_module.build_link_index

    def counting_build(*args, **kwargs):
        calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(index_module, "build_link_index", counting_build)

    for _ in range(3):
        retrieve(index, "shortest paths priority queue", vault, k=4)
    assert calls["n"] == 1, f"link index rebuilt {calls['n']} times across 3 identical queries"

    (vault / "50-Reference" / "extra.md").write_text(
        "---\ntitle: Extra\n---\n\nA brand new note about Kruskal. See [[Mom]].\n",
        encoding="utf-8",
    )
    index.upsert_file(vault, "50-Reference/extra.md")

    retrieve(index, "shortest paths priority queue", vault, k=4)
    assert calls["n"] == 2, "link index cache survived a mutation"


def test_build_link_index_not_called_when_no_hit_has_wikilinks(
    index, vault: Path, monkeypatch
) -> None:
    from backend.rag.retrieve import retrieve

    calls = {"n": 0}
    real_link_index = index.link_index

    def counting_link_index(*args, **kwargs):
        calls["n"] += 1
        return real_link_index(*args, **kwargs)

    monkeypatch.setattr(index, "link_index", counting_link_index)

    # Mom.md has no wikilinks of its own, and this query is specific enough
    # that it (not the [[Mom]]-linking algorithms.md note) should be the
    # only/top hit.
    retrieve(index, "Mom's birthday is in March", vault, k=1, expand_links=True)

    assert calls["n"] == 0, "link index built even though the top-k had no wikilinks"
