"""Pydantic models for the reranker stage's output.

Kept as Pydantic models per CLAUDE.md's "structured outputs via Pydantic"
convention, matching `retrieval.models`/`indexing.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from codebase_rag_mcp.retrieval.models import HybridQueryResult


class RerankedResult(BaseModel):
    """One reordered result from `rerank`.

    Wraps the full input `HybridQueryResult` under `hybrid_result` -- never
    flattened -- mirroring how `HybridQueryResult` itself nests the full
    `Chunk` under `chunk` rather than flattening `Chunk`'s fields onto
    itself (retrieval/models.py). `rerank_score` (a raw CrossEncoder logit --
    unbounded, NOT on the same scale as `hybrid_result.score` and never
    combined with it, see DECISIONS.md) and `rerank_rank` (1-indexed
    position after reordering, the same convention `HybridQueryResult`'s RRF
    ranks use) are the only two fields this stage adds.
    """

    model_config = ConfigDict(frozen=True)

    hybrid_result: HybridQueryResult
    rerank_score: float
    rerank_rank: int


__all__ = ["RerankedResult"]
