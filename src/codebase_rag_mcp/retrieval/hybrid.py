"""Hybrid retrieval: merge BM25 + vector candidates via Reciprocal Rank
Fusion (RRF).

RRF was chosen over a weighted-sum-of-normalized-scores because BM25 scores
are unbounded/corpus-dependent while vector scores are bounded cosine
similarities in `[-1, 1]` -- RRF only needs each list's *rank order*, which
is already well-defined on both sides, avoiding the need to normalize two
incomparable scales. See DECISIONS.md for the full rationale.
"""

from __future__ import annotations

import logging
from pathlib import Path

from codebase_rag_mcp.config import HYBRID_CANDIDATE_POOL_SIZE, INDEX_DIR, RRF_K
from codebase_rag_mcp.indexing import bm25, vector
from codebase_rag_mcp.indexing.exceptions import (
    Bm25LoadError,
    Bm25NotBuiltError,
    EmptyBm25IndexError,
    EmptyIndexError,
    IndexLoadError,
    IndexNotBuiltError,
)
from codebase_rag_mcp.indexing.models import Bm25QueryResult, VectorQueryResult
from codebase_rag_mcp.retrieval.exceptions import NoIndexAvailableError
from codebase_rag_mcp.retrieval.models import HybridQueryResult

logger = logging.getLogger(__name__)

_BM25_UNAVAILABLE = (Bm25NotBuiltError, Bm25LoadError, EmptyBm25IndexError)
_VECTOR_UNAVAILABLE = (IndexNotBuiltError, IndexLoadError, EmptyIndexError)


def _reciprocal_rank_fusion(
    bm25_results: list[Bm25QueryResult],
    vector_results: list[VectorQueryResult],
    *,
    k: int = RRF_K,
) -> list[HybridQueryResult]:
    """Merge two ranked candidate lists via RRF.

    `rank` is **1-indexed** (`enumerate(results, start=1)`): the top
    (highest-scoring) entry in each input list has `rank = 1`, never `0`.
    A chunk present in a list at rank `r` contributes `1 / (k + r)` to its
    merged score; a chunk absent from a list contributes nothing to the
    sum, and that side's rank/score fields stay `None` on the result, never
    a fabricated `0`. The merged list is sorted by score, descending.
    """
    by_id: dict[str, HybridQueryResult] = {}

    for rank, result in enumerate(bm25_results, start=1):
        by_id[result.chunk.id] = HybridQueryResult(
            chunk=result.chunk,
            score=1.0 / (k + rank),
            bm25_rank=rank,
            bm25_score=result.score,
        )

    for rank, vec_result in enumerate(vector_results, start=1):
        contribution = 1.0 / (k + rank)
        existing = by_id.get(vec_result.chunk.id)
        if existing is None:
            by_id[vec_result.chunk.id] = HybridQueryResult(
                chunk=vec_result.chunk,
                score=contribution,
                vector_rank=rank,
                vector_score=vec_result.score,
            )
        else:
            by_id[vec_result.chunk.id] = existing.model_copy(
                update={
                    "score": existing.score + contribution,
                    "vector_rank": rank,
                    "vector_score": vec_result.score,
                }
            )

    return sorted(by_id.values(), key=lambda r: r.score, reverse=True)


def hybrid_search(
    query: str,
    *,
    top_k: int = 10,
    candidate_pool_size: int = HYBRID_CANDIDATE_POOL_SIZE,
    index_dir: str | Path = INDEX_DIR,
    rrf_k: int = RRF_K,
) -> list[HybridQueryResult]:
    """Query both indexes, merge via RRF, return the top `top_k` results.

    Loads both indexes fresh each call via `bm25.load_index`/
    `vector.load_index` (no caching -- Day 08's MCP server owns lifecycle
    once it exists). Raises `NoIndexAvailableError` only if *neither* index
    has ever been built under `index_dir` -- a caller must be able to tell
    "nothing indexed yet" apart from "indexed, but this query has no real
    match." If exactly one side is unavailable, logs a warning and proceeds
    hybrid-search-as-single-source.

    The vector side's raw `VectorIndex.query` never thresholds by design
    (Day 04) -- it always returns up to `top_k` neighbours regardless of
    relevance. That floor (`score > 0.0`) is applied here in the retrieval
    layer instead, mirroring the floor `Bm25Index.query` already applies
    internally -- this is what lets a query with zero real matches on
    either side return an explicitly empty merged result, without changing
    `vector.py`'s established contract.
    """
    bm25_results: list[Bm25QueryResult] = []
    vector_results: list[VectorQueryResult] = []
    bm25_available = True
    vector_available = True

    try:
        bm25_index = bm25.load_index(index_dir=index_dir)
        bm25_results = bm25_index.query(query, top_k=candidate_pool_size)
    except _BM25_UNAVAILABLE as exc:
        bm25_available = False
        logger.warning("BM25 index unavailable under %s: %s", index_dir, exc)

    try:
        vector_index = vector.load_index(index_dir=index_dir)
        raw_vector_results = vector_index.query(query, top_k=candidate_pool_size)
        vector_results = [r for r in raw_vector_results if r.score > 0.0]
    except _VECTOR_UNAVAILABLE as exc:
        vector_available = False
        logger.warning("Vector index unavailable under %s: %s", index_dir, exc)

    if not bm25_available and not vector_available:
        raise NoIndexAvailableError(
            f"neither a BM25 nor a vector index is built/loadable under {index_dir}"
        )

    merged = _reciprocal_rank_fusion(bm25_results, vector_results, k=rrf_k)
    return merged[:top_k]


__all__ = ["hybrid_search"]
