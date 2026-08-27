"""Pydantic models for the citations stage's output.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `reranker.models`/`retrieval.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Citation(BaseModel):
    """One citation attached to a generated answer.

    Unlike `HybridQueryResult`/`RerankedResult`, this model is a deliberately
    *flat* projection of `Chunk` rather than a nested wrapper -- a citation is
    a small, tool/answer-facing summary, not an evidence-inspection object
    (see DECISIONS.md D-022). Every field here is always copied from the
    matching `candidate.hybrid_result.chunk` in `attach.attach_citations`,
    **never** from anything an LLM asserts -- this is the concrete mechanism
    behind "no fabricated citations": the model only ever supplies which
    `chunk_id` it used, this project's own indexed metadata supplies
    everything else about the citation.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    file: str
    symbol: str
    start_line: int
    end_line: int


__all__ = ["Citation"]
