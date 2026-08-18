"""Tests for hybrid retrieval fixes (needs the [rag] extra).

Covers the four retrieve.py defects fixed alongside the latency fix:
filter starvation, similarity-blind fusion, unbounded link expansion, and
asymmetric recency. See backend/rag/retrieve.py's module + constant-block
docstrings for the reasoning behind each fix.
"""

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
    """Same shape as tests/rag/test_index.py's vault fixture, kept independent
    on purpose (see that module's docstring on why the two test files don't
    share fixtures)."""
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
    (root / "10-Daily" / "undated.md").write_text(
        "Studied Dijkstra's shortest path algorithm today, no frontmatter date.\n",
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


@pytest.fixture()
def courses_vault(tmp_path: Path) -> Path:
    """A vault with two courses, engineered so the global (unfiltered) top-20
    pool for the shared query is entirely course Y — the exact starvation
    scenario described in the ground truth: "a course= query whose global
    top-20 is all other courses returns near-zero hits while matching chunks
    sit in the index." """
    root = tmp_path / "vault"
    (root / "15-Courses" / "CSY" / "notes").mkdir(parents=True)
    (root / "15-Courses" / "CSX" / "notes").mkdir(parents=True)

    # 25 near-duplicate CSY notes flood both the BM25 and vector top-20 for
    # the shared query, so a course=CSX query has nothing left in an
    # unfiltered pool if filtering only happens after the pool is cut.
    for i in range(25):
        (root / "15-Courses" / "CSY" / "notes" / f"note{i}.md").write_text(
            f"Lecture {i}: gradient descent optimization neural network training "
            "is the core topic of this course.\n",
            encoding="utf-8",
        )
    (root / "15-Courses" / "CSX" / "notes" / "note.md").write_text(
        "In statistics, gradient descent is one of several optimization "
        "techniques used for parameter estimation.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def courses_index(courses_vault: Path, tmp_path: Path):
    from backend.rag.index import VaultIndex

    vault_index = VaultIndex(tmp_path / "chroma-courses")
    vault_index.reindex_all(courses_vault)
    return vault_index


def test_course_filter_survives_pool_starvation(courses_index, courses_vault: Path) -> None:
    """THE STARVATION REGRESSION: a course=CSX query must not come back empty
    just because course CSY dominates the unfiltered global top-20 pool."""
    from backend.rag.retrieve import retrieve_result

    result = retrieve_result(
        courses_index,
        "gradient descent optimization neural network training",
        courses_vault,
        k=8,
        course="CSX",
        expand_links=False,
    )

    assert result.results, "course=CSX query starved despite a matching CSX note in the index"
    assert all(hit["meta"]["course"] == "CSX" for hit in result.results)


def test_nonsense_query_returns_empty(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve_result

    result = retrieve_result(
        index,
        "purple bicycle marmalade astrophysics jazz quintet Tuesday",
        vault,
        k=8,
        expand_links=False,
    )

    assert result.results == []


def test_results_bounded_by_k_and_related_holds_link_expansions(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve_result

    result = retrieve_result(
        index, "shortest paths priority queue", vault, k=1, expand_links=True
    )

    assert len(result.results) <= 1
    assert all(not hit["meta"].get("linked") for hit in result.results)
    linked_paths = [hit["meta"]["path"] for hit in result.related]
    assert "Mom.md" in linked_paths
    assert all(hit["meta"].get("kind") == "link" for hit in result.related)


def test_retrieve_shim_returns_results_plus_related(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve, retrieve_result

    result = retrieve_result(index, "shortest paths priority queue", vault, k=4)
    combined = retrieve(index, "shortest paths priority queue", vault, k=4)

    assert combined == result.results + result.related


def test_undated_note_ranks_below_dated_fresh_note(index, vault: Path) -> None:
    """Inverse of the old bug: an undated daily note used to get the old
    `except ValueError: return 1.0` no-penalty path and could outrank a
    dated, genuinely fresh note. Now it gets RECENCY_UNDATED_MULTIPLIER."""
    from backend.rag.retrieve import retrieve_result

    result = retrieve_result(
        index,
        "Dijkstra shortest path studied today",
        vault,
        k=8,
        expand_links=False,
        today=date(2026, 7, 12),
    )
    scores_by_path = {hit["meta"]["path"]: hit["score"] for hit in result.results}

    fresh = scores_by_path.get("10-Daily/2026-07-11.md")
    undated = scores_by_path.get("10-Daily/undated.md")
    assert fresh is not None and undated is not None, "expected both notes to be retrieved"
    assert undated < fresh, "undated note must not outrank a dated, fresh note"


def test_old_dated_note_is_demoted_not_deleted(index, vault: Path) -> None:
    """The recency floor: an old dated note can still be retrieved, just
    ranked below a fresh one on the same topic — not erased entirely."""
    from backend.rag.retrieve import retrieve_result

    result = retrieve_result(
        index,
        "Dijkstra daily study",
        vault,
        k=8,
        expand_links=False,
        today=date(2026, 7, 12),
    )
    paths = [hit["meta"]["path"] for hit in result.results]

    assert "10-Daily/2025-01-01.md" in paths, "old note was deleted, not demoted"
    assert paths.index("10-Daily/2026-07-11.md") < paths.index("10-Daily/2025-01-01.md")


def test_rerank_off_by_default(index, vault: Path) -> None:
    from backend.rag.retrieve import retrieve_result

    without_flag = retrieve_result(
        index, "shortest paths priority queue", vault, k=4, expand_links=False
    )
    explicit_off = retrieve_result(
        index, "shortest paths priority queue", vault, k=4, expand_links=False, rerank=False
    )

    assert [h["meta"]["path"] for h in without_flag.results] == [
        h["meta"]["path"] for h in explicit_off.results
    ]


def test_rerank_failure_falls_back_to_fusion_order(monkeypatch) -> None:
    """Model unavailable / import error / scoring exception must never
    propagate out of rerank() — it degrades to the incoming fusion order."""
    from backend.rag import rerank as rerank_module

    fused_order = ["a", "b", "c"]
    hits = [{"text": t, "meta": {"path": t}, "score": 1.0} for t in fused_order]

    def failing_model():
        raise RuntimeError("HF_HUB_OFFLINE and nothing cached")

    monkeypatch.setattr(rerank_module, "_get_model", failing_model)
    result = rerank_module.rerank("query", hits, k=2)

    assert [h["meta"]["path"] for h in result] == fused_order[:2]


def test_retrieve_result_with_rerank_enabled_does_not_raise_on_model_failure(
    index, vault: Path, monkeypatch
) -> None:
    """Integration-level guarantee: retrieve_result(rerank=True) must degrade
    to fusion order rather than raise, even if the cross-encoder can't load
    (e.g. no network / weights not staged)."""
    from backend.rag import rerank as rerank_module
    from backend.rag.retrieve import retrieve_result

    def failing_model():
        raise RuntimeError("no network / weights not staged")

    monkeypatch.setattr(rerank_module, "_get_model", failing_model)

    result = retrieve_result(
        index, "shortest paths priority queue", vault, k=4, expand_links=False, rerank=True
    )

    assert result.results, "rerank failure must not empty out real results"
