"""Reranker: cross-encoder reordering of a hybrid candidate pool.

`reranker.rerank.rerank` consumes `retrieval.hybrid.hybrid_search`'s output
(a wide-`top_k` candidate pool, per the calling contract -- see `rerank.py`'s
module docstring and DECISIONS.md) and reorders it by cross-encoder
relevance, returning the top `RERANK_TOP_N` as `RerankedResult`s. This is
the last retrieval-side stage before Day 07's generation.
"""

from __future__ import annotations

from codebase_rag_mcp.reranker.exceptions import RerankerError, RerankerModelError
from codebase_rag_mcp.reranker.models import RerankedResult
from codebase_rag_mcp.reranker.rerank import rerank

__all__ = ["RerankedResult", "RerankerError", "RerankerModelError", "rerank"]
