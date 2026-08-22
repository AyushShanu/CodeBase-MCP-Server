"""Pydantic models for the retrieval stage's output.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `indexing.models`/`chunker.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from codebase_rag_mcp.chunker.models import Chunk


class HybridQueryResult(BaseModel):
    """One merged result from `hybrid_search`'s Reciprocal Rank Fusion.

    Carries the full `Chunk` (never a stripped-down projection) plus each
    contributing side's rank/score, so a caller/test can see *why* a chunk
    ranked where it did (CLAUDE.md's "transparent scoring"), not just the
    final number. A side that did not surface this chunk has its rank/score
    left `None` -- never a fabricated `0`, which would be indistinguishable
    from a genuine top rank on that side.
    """

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None


__all__ = ["HybridQueryResult"]
