"""Retrieval: hybrid query routing across the dense and sparse indexes.

`hybrid_search` merges `indexing.bm25`'s `BM25Okapi` candidates and
`indexing.vector`'s FAISS candidates via Reciprocal Rank Fusion into one
ranked, transparently-scored `list[HybridQueryResult]`. See DECISIONS.md
for the merge-strategy rationale (RRF vs. weighted-sum).
"""

from __future__ import annotations

from codebase_rag_mcp.retrieval.exceptions import NoIndexAvailableError, RetrievalError
from codebase_rag_mcp.retrieval.hybrid import hybrid_search
from codebase_rag_mcp.retrieval.models import HybridQueryResult

__all__ = [
    "HybridQueryResult",
    "NoIndexAvailableError",
    "RetrievalError",
    "hybrid_search",
]
