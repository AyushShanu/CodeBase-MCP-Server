"""Typed exceptions for the retrieval stage (hybrid_search).

Every error `hybrid.py` can raise is a subclass of `RetrievalError` so
callers can catch broadly (`except RetrievalError`) or narrowly
(`except NoIndexAvailableError`) as needed.
"""

from __future__ import annotations


class RetrievalError(Exception):
    """Base class for all retrieval-stage errors."""


class NoIndexAvailableError(RetrievalError):
    """Raised by `hybrid_search` when neither the BM25 nor the vector index
    has ever been built/loadable under the given `index_dir`.

    A query against two genuinely built indexes that happens to match
    nothing returns an empty result list instead of raising -- callers must
    be able to tell "nothing indexed yet" apart from "indexed, but this
    query has no evidence" (Day 07's "not enough evidence" fallback depends
    on this distinction).
    """


__all__ = ["NoIndexAvailableError", "RetrievalError"]
