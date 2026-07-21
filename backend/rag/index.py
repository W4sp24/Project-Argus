"""Embedding + vector store for the vault (ChromaDB, local embeddings).

Everything stays on this machine: bge-small-en-v1.5 runs on CPU and ChromaDB
persists under the vault's ``.argus/chroma`` directory. IDs are
``sha1(path::index)`` so re-indexing a file is an idempotent delete+add.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from backend.rag.chunk import Chunk, chunk_blocks
from backend.rag.extract import extract_blocks
from backend.rag.paths import is_indexable

# A HuggingFace repo id by default (downloaded on first use). The packaged
# desktop app pre-bakes the weights and points this at an absolute path so a
# fresh install never depends on a network fetch that can rate-limit.
MODEL_NAME = os.environ.get("ARGUS_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
COLLECTION = "vault"
EMBED_BATCH = 64


def _chunk_id(rel_path: str, index: int) -> str:
    return hashlib.sha1(f"{rel_path}::{index}".encode()).hexdigest()


class VaultIndex:
    """Persistent local index over one vault. Heavy deps load lazily."""

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir
        self._model: Any = None
        self._collection: Any = None

    @property
    def collection(self) -> Any:
        if self._collection is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(self._db_dir))
            self._collection = client.get_or_create_collection(
                COLLECTION, metadata={"hnsw:space": "cosine"}
            )
        return self._collection

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

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        """(Re-)index one file; returns the number of chunks stored."""
        if not is_indexable(rel_path):
            return 0
        self.delete_file(rel_path)
        file_path = vault_path / rel_path
        if not file_path.is_file():
            return 0
        chunks = chunk_blocks(extract_blocks(file_path), rel_path)
        if not chunks:
            return 0
        self.collection.add(
            ids=[_chunk_id(rel_path, i) for i in range(len(chunks))],
            documents=[chunk.text for chunk in chunks],
            embeddings=self._embed([chunk.text for chunk in chunks]),
            metadatas=[chunk.meta for chunk in chunks],
        )
        return len(chunks)

    def reindex_all(self, vault_path: Path) -> dict[str, int]:
        """Full rebuild. Returns {rel_path: chunk_count} for indexed files."""
        counts: dict[str, int] = {}
        for file_path in sorted(vault_path.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(vault_path).as_posix()
            if not is_indexable(rel_path):
                continue
            count = self.upsert_file(vault_path, rel_path)
            if count:
                counts[rel_path] = count
        return counts

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
        """Every stored chunk (for the BM25 corpus)."""
        if self.collection.count() == 0:
            return []
        result = self.collection.get(include=["documents", "metadatas"])
        return [
            {"text": document, "meta": meta}
            for document, meta in zip(result["documents"], result["metadatas"], strict=True)
        ]


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
