"""Embedding + vector store for the vault (ChromaDB, local embeddings).

Everything stays on this machine: bge-small-en-v1.5 runs on CPU and ChromaDB
persists under the vault's ``.argus/chroma`` directory. IDs are
``sha1(path::index)`` so re-indexing a file is an idempotent delete+add.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.chunk import Chunk, chunk_blocks
from backend.rag.extract import extract_blocks
from backend.vault.paths import is_indexable

logger = logging.getLogger("argus.rag")

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())

# A HuggingFace repo id by default (downloaded on first use). The packaged
# desktop app pre-bakes the weights and points this at an absolute path so a
# fresh install never depends on a network fetch that can rate-limit.
MODEL_NAME = os.environ.get("ARGUS_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
COLLECTION = "vault"
EMBED_BATCH = 64

# Bump this when the chunk shape changes (fields added/removed/renamed on
# Chunk.meta, or the windowing strategy changes) so that an index built under
# the old shape gets rebuilt instead of silently degrading retrieval forever.
# Stored in the chroma collection's metadata; see ``schema_stale`` for why a
# plain ``get_or_create_collection(metadata=...)`` bump is not enough.
#
# Bumped 1 -> 2 for the retrieval-quality fix: chunking switched from
# word-window to line-window (chunk text now keeps newlines and starts with
# its own heading), chunk.meta gained "breadcrumb", and "tags" now unions
# inline #tags with frontmatter tags. An index built under version 1 has
# run-on, heading-less chunk text and misses every inline tag, so it must be
# rebuilt rather than mixed in with new-shape chunks.
SCHEMA_VERSION = 2
_COLLECTION_METADATA = {"hnsw:space": "cosine", "schema_version": SCHEMA_VERSION}


def _chunk_id(rel_path: str, index: int) -> str:
    return hashlib.sha1(f"{rel_path}::{index}".encode()).hexdigest()


@dataclass
class ReindexResult:
    """Outcome of a full :meth:`VaultIndex.reindex_all` run.

    ``counts`` mirrors the pre-existing ``{rel_path: chunk_count}`` shape
    (only files that produced at least one chunk are present). ``errors`` maps
    a vault-relative path to the reason it failed — a single unreadable PDF
    must not silently vanish from a rebuild that otherwise looks complete.
    """

    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_chunks(self) -> int:
        return sum(self.counts.values())

    @property
    def files(self) -> int:
        return len(self.counts)


class VaultIndex:
    """Persistent local index over one vault. Heavy deps load lazily."""

    def __init__(self, db_dir: Path, *, taxonomy: Taxonomy | None = None) -> None:
        self._db_dir = db_dir
        self._taxonomy = taxonomy or active_taxonomy()
        self._model: Any = None
        self._collection: Any = None
        # `all_chunks()`/`bm25()` cache: a full `collection.get()` of every
        # document+metadata in the vault is O(whole vault), and retrieve()
        # used to pay that cost on every single query to rebuild the BM25
        # corpus. `_version` bumps on every mutation (upsert/delete/recreate);
        # the cache is valid as long as (_version, collection.count()) still
        # matches what was cached — cheap to check, and catches any mutation
        # that goes through this class's own methods.
        self._version = 0
        self._chunks_cache: list[dict] | None = None
        self._chunks_cache_key: tuple[int, int] | None = None
        self._bm25_cache: Any = None
        self._bm25_cache_key: tuple[int, int] | None = None

    def _invalidate_cache(self) -> None:
        self._version += 1
        self._chunks_cache = None
        self._chunks_cache_key = None
        self._bm25_cache = None
        self._bm25_cache_key = None

    def _client(self) -> Any:
        import chromadb

        return chromadb.PersistentClient(path=str(self._db_dir))

    @property
    def collection(self) -> Any:
        if self._collection is None:
            self._collection = self._client().get_or_create_collection(
                COLLECTION, metadata=dict(_COLLECTION_METADATA)
            )
        return self._collection

    def schema_stale(self) -> bool:
        """True when the persisted collection predates the current chunk schema.

        ``chromadb.get_or_create_collection(..., metadata=...)`` does **not**
        update the metadata of an already-existing collection — it just
        returns it as stored. So a collection created before ``schema_version``
        existed (every v0.2.0 index) has no such key at all, and one created
        under an older ``SCHEMA_VERSION`` keeps its old value forever unless
        something explicitly rebuilds it. Both cases must count as stale:
        absent is exactly what "predates this field" looks like.
        """
        meta = self.collection.metadata or {}
        return meta.get("schema_version") != SCHEMA_VERSION

    def _recreate_collection(self) -> None:
        """Drop and recreate the collection under the current schema metadata.

        Necessary (not just tidy) when the schema is stale: old-shape chunks
        may not carry the same metadata keys ``delete_file``'s ``where``
        clause relies on, so deleting file-by-file during a rebuild could
        leave orphaned old-shape chunks behind. Dropping the whole collection
        guarantees none survive, and re-passing ``hnsw:space: cosine``
        explicitly here is what keeps that setting from being lost — a bare
        ``create_collection`` with no metadata would silently fall back to
        chroma's default (squared L2) distance.
        """
        client = self._client()
        with contextlib.suppress(Exception):
            client.delete_collection(COLLECTION)
        self._collection = client.get_or_create_collection(
            COLLECTION, metadata=dict(_COLLECTION_METADATA)
        )
        self._invalidate_cache()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(MODEL_NAME)
        vectors = self._model.encode(
            texts, batch_size=EMBED_BATCH, normalize_embeddings=True, show_progress_bar=False
        )
        return [vector.tolist() for vector in vectors]

    def delete_file(self, rel_path: str) -> None:
        """Remove every chunk of one vault-relative file from the index."""
        self.collection.delete(where={"path": rel_path})
        self._invalidate_cache()

    def upsert_file(
        self, vault_path: Path, rel_path: str, *, errors: list[str] | None = None
    ) -> int:
        """(Re-)index one file; returns the number of chunks stored.

        ``errors``, when given, collects a message if extraction failed for
        this file (still returns 0 — one bad file must never abort a caller
        that indexes many, e.g. :meth:`reindex_all`).
        """
        if not is_indexable(rel_path, taxonomy=self._taxonomy):
            return 0
        self.delete_file(rel_path)
        file_path = vault_path / rel_path
        if not file_path.is_file():
            return 0
        blocks = extract_blocks(file_path, errors=errors)
        chunks = chunk_blocks(blocks, rel_path, taxonomy=self._taxonomy)
        if not chunks:
            return 0
        self.collection.add(
            ids=[_chunk_id(rel_path, i) for i in range(len(chunks))],
            documents=[chunk.text for chunk in chunks],
            embeddings=self._embed([chunk.text for chunk in chunks]),
            metadatas=[chunk.meta for chunk in chunks],
        )
        self._invalidate_cache()
        return len(chunks)

    def reindex_all(self, vault_path: Path) -> ReindexResult:
        """Full rebuild. Returns per-file counts, a total, and any errors.

        Forces a clean rebuild of the collection itself when the persisted
        schema is stale (see :meth:`schema_stale`) — otherwise old-shape
        chunks left over from a prior chunker could silently coexist with new
        ones and degrade retrieval instead of being replaced by it.
        """
        if self.schema_stale():
            logger.info("chroma schema stale (or absent) — recreating collection")
            self._recreate_collection()

        counts: dict[str, int] = {}
        errors: dict[str, str] = {}
        for file_path in sorted(vault_path.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(vault_path).as_posix()
            if not is_indexable(rel_path, taxonomy=self._taxonomy):
                continue
            file_errors: list[str] = []
            try:
                count = self.upsert_file(vault_path, rel_path, errors=file_errors)
            except Exception as exc:  # a broken embed/store call must not abort the rebuild
                logger.warning("reindex failed for %s: %s", rel_path, exc)
                errors[rel_path] = str(exc)
                continue
            if file_errors:
                message = "; ".join(file_errors)
                logger.warning("extraction failed for %s: %s", rel_path, message)
                errors[rel_path] = message
            if count:
                counts[rel_path] = count
        return ReindexResult(counts=counts, errors=errors)

    def size(self) -> dict[str, int]:
        """``{chunks, files}`` for the status endpoint — no embedding model load.

        Only touches the chromadb client/collection, never ``_embed``, so this
        is cheap enough to call on every status poll.
        """
        total = self.collection.count()
        if total == 0:
            return {"chunks": 0, "files": 0}
        result = self.collection.get(include=["metadatas"])
        files = {meta.get("path") for meta in result["metadatas"] if meta.get("path")}
        return {"chunks": total, "files": len(files)}

    def query(self, text: str, n_results: int = 20, where: dict | None = None) -> list[dict]:
        """Vector search returning [{text, meta, score}] (higher = closer)."""
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=self._embed([text]),
            n_results=min(n_results, self.collection.count()),
            where=where or None,
        )
        hits: list[dict] = []
        for document, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            hits.append({"text": document, "meta": meta, "score": 1.0 - distance})
        return hits

    def all_chunks(self) -> list[dict]:
        """Every stored chunk (for the BM25 corpus). Memoised — see ``__init__``.

        A real vault's ``collection.get()`` of every document+metadata is
        O(whole vault); ``retrieve()`` used to call this once per query, which
        made search cost grow with vault size instead of query cost. The
        cache key is ``(_version, collection.count())`` so any mutation this
        class knows about (upsert/delete/recreate) forces a fresh fetch, and a
        stray external mutation would still be caught by the count changing.
        """
        count = self.collection.count()
        key = (self._version, count)
        if self._chunks_cache is not None and self._chunks_cache_key == key:
            return self._chunks_cache
        if count == 0:
            chunks: list[dict] = []
        else:
            result = self.collection.get(include=["documents", "metadatas"])
            chunks = [
                {"text": document, "meta": meta}
                for document, meta in zip(result["documents"], result["metadatas"], strict=True)
            ]
        self._chunks_cache = chunks
        self._chunks_cache_key = key
        return chunks

    def bm25(self) -> Any:
        """Cached ``rank_bm25.BM25Okapi`` over :meth:`all_chunks`, or ``None`` when empty.

        Rebuilt only when the underlying corpus actually changed (same cache
        key as ``all_chunks``) — the whole point being that repeated queries
        against an unchanged index build the BM25 corpus exactly once.
        """
        chunks = self.all_chunks()
        key = self._chunks_cache_key
        if self._bm25_cache is not None and self._bm25_cache_key == key:
            return self._bm25_cache
        bm25 = None
        if chunks:
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi([_tokenize(chunk["text"]) for chunk in chunks])
        self._bm25_cache = bm25
        self._bm25_cache_key = key
        return bm25


def index_chunks(index: VaultIndex, rel_path: str, chunks: list[Chunk]) -> None:
    """Store pre-built chunks (test seam and future partial updates)."""
    if not chunks:
        return
    index.collection.add(
        ids=[_chunk_id(rel_path, i) for i in range(len(chunks))],
        documents=[chunk.text for chunk in chunks],
        embeddings=index._embed([chunk.text for chunk in chunks]),
        metadatas=[chunk.meta for chunk in chunks],
    )
    index._invalidate_cache()
