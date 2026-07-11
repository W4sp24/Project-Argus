"""Local RAG pipeline: extract -> chunk -> embed/store -> retrieve.

Heavy dependencies (sentence-transformers, chromadb, document extractors) are
imported lazily inside the modules that need them, so the base app runs without
the ``[rag]`` extra installed.
"""
