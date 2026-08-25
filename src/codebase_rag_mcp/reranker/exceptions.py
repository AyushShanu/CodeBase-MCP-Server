"""Typed exceptions for the reranker stage (reranker.rerank.rerank).

Every error `rerank.py` can raise is a subclass of `RerankerError` so
callers can catch broadly (`except RerankerError`) or narrowly
(`except RerankerModelError`) as needed.
"""

from __future__ import annotations


class RerankerError(Exception):
    """Base class for all reranker-stage errors."""


class RerankerModelError(RerankerError):
    """Raised when the cross-encoder model fails to load or `.predict()`
    raises while scoring a batch of (query, chunk) pairs."""


__all__ = ["RerankerError", "RerankerModelError"]
