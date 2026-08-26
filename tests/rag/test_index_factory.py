"""The shared-instance index factory (backend.rag.index.make_index_factory).

Deliberately *not* marked with tests/rag/test_index.py's ``HAS_RAG`` skip:
constructing a ``VaultIndex`` only sets attributes — chromadb and
sentence-transformers load lazily on first use — so this suite runs
everywhere, which is the point. The behaviour it pins is about instance
identity, not about embedding anything.
"""

from __future__ import annotations

import threading
from pathlib import Path

from backend.rag.index import VaultIndex, make_index_factory


def test_factory_returns_the_same_instance_every_call(tmp_path: Path) -> None:
    """The whole point: one embedding model load per process, not one per call.

    Before this existed, ``backend.main._default_index_factory`` built a fresh
    ``VaultIndex`` on every call, so every upload, every search and the
    watcher each paid the ~7s SentenceTransformer load again.
    """
    factory = make_index_factory(tmp_path / "chroma")

    first = factory()
    second = factory()

    assert isinstance(first, VaultIndex)
    assert first is second


def test_separate_factories_do_not_share(tmp_path: Path) -> None:
    """Two vaults (or two test apps) must not be handed one another's index."""
    assert make_index_factory(tmp_path / "a")() is not make_index_factory(tmp_path / "b")()


def test_concurrent_first_calls_still_yield_one_instance(tmp_path: Path) -> None:
    """The watcher thread and a request thread can both call this first.

    Without a lock they both observe an empty cache and construct their own,
    which is the per-call cost this factory exists to remove — and, worse,
    two instances over one chroma directory cannot see each other's upserts,
    because the all_chunks/BM25/link caches are keyed on a ``_version``
    counter each instance bumps only on its own mutations.
    """
    factory = make_index_factory(tmp_path / "chroma")
    seen: list[VaultIndex] = []
    barrier = threading.Barrier(8)

    def race() -> None:
        barrier.wait()
        seen.append(factory())

    threads = [threading.Thread(target=race) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 8
    assert len({id(index) for index in seen}) == 1
