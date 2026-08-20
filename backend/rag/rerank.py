"""Optional cross-encoder reranking of the fused retrieval pool.

Off by default — ``Settings.rerank_enabled`` (``ARGUS_RAG_RERANK`` in
``.env``) gates whether ``retrieve_result`` ever calls into this module. Both
call sites pass it through the ``retrieve`` shim, which for a while dropped the
flag: the claim in this sentence was true of ``retrieve_result``'s signature and
false of anything that actually ran.
``sentence-transformers`` (already an ``[rag]`` dependency for the embedding
model) ships ``CrossEncoder``, so this needs no new package.

Follow-up for the packaged desktop app: turning this on there needs the
cross-encoder weights pre-baked the same way the embedding model already is,
in all six places that currently know about ``MODEL_NAME`` /
``ARGUS_EMBED_MODEL``:

* ``.github/workflows/_package.yml`` (CI model cache step)
* ``desktop/scripts/stage.mjs`` (stages weights into the packaged app)
* ``desktop/lib/paths.js`` (resolves the staged model path)
* ``desktop/main.js`` (sets the env var pointing at it)
* ``desktop/argus-backend.spec`` (PyInstaller data files)

``HF_HUB_OFFLINE=1`` is process-global, not per-model — so a half-staged
cross-encoder (env var set, weights not actually bundled) would fail hard
instead of degrading, unlike this module's own in-process fallback below.
That risk is exactly why reranking defaults off and its weights are not
bundled in this commit; wiring all six sites is a separate piece of work.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("argus.rag")

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# How much of the fused pool is worth handing to the cross-encoder. Larger
# than k so reranking can actually promote a hit RRF ranked outside the top
# k, but bounded — scoring the whole pool at O(pool) forward passes per query
# is the cost this knob controls.
RERANK_POOL = 30

_model: Any = None


def _get_model() -> Any:
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(MODEL_NAME)
    return _model


def rerank(query: str, hits: list[dict], k: int) -> list[dict]:
    """Re-order ``hits`` (fusion order, best first) by cross-encoder relevance.

    Falls back to the incoming fusion order on ANY failure — model weights
    unavailable, ``HF_HUB_OFFLINE=1`` with nothing cached, import error,
    exception mid-scoring. Reranking is a quality knob, not a correctness
    dependency: retrieval must never hard-fail just because the cross-encoder
    couldn't run.
    """
    if not hits:
        return hits[:k]
    pool = hits[:RERANK_POOL]
    try:
        model = _get_model()
        pairs = [(query, hit["text"]) for hit in pool]
        ce_scores = model.predict(pairs)
        pool = [
            hit
            for _, hit in sorted(
                zip(ce_scores, pool, strict=True), key=lambda pair: pair[0], reverse=True
            )
        ]
    except Exception:
        logger.debug("rerank unavailable, falling back to fusion order", exc_info=True)
        return hits[:k]
    return pool[:k]
