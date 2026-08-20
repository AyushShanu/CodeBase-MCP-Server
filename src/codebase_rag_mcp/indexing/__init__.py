"""Indexing: dense vector and sparse BM25 indexes over chunks.

Submodules:

- :mod:`codebase_rag_mcp.indexing.vector` -- FAISS-backed dense index
- :mod:`codebase_rag_mcp.indexing.bm25` -- rank-bm25 sparse index

Not implemented yet. Each module will own build/persist/load/append APIs.
"""

from codebase_rag_mcp.indexing import bm25, vector

__all__ = ["bm25", "vector"]
