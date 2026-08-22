"""Typed exceptions for the indexing stage (embedding + vector index).

Every error `vector.py`/`repo.py` can raise is a subclass of `IndexingError`
so callers can catch broadly (`except IndexingError`) or narrowly
(`except EmptyIndexError`) as needed.
"""

from __future__ import annotations


class IndexingError(Exception):
    """Base class for all indexing-related errors."""


class EmbeddingModelError(IndexingError):
    """Raised when the embedding model fails to load or embed a batch of text."""


class EmptyIndexError(IndexingError):
    """Raised when a build or load would leave a queryable index with zero vectors."""


class IndexNotBuiltError(IndexingError):
    """Raised by `load_index` when `index_dir` has no persisted index files yet."""


class IndexLoadError(IndexingError):
    """Raised when a persisted index/metadata file exists but is unreadable or inconsistent."""


__all__ = [
    "EmbeddingModelError",
    "EmptyIndexError",
    "IndexLoadError",
    "IndexNotBuiltError",
    "IndexingError",
]
