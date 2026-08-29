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


class Bm25NotBuiltError(IndexingError):
    """Raised by `bm25.load_index` when `index_dir` has no persisted BM25 index files yet."""


class Bm25LoadError(IndexingError):
    """Raised when a persisted BM25 index/metadata file exists but is unreadable,
    unpicklable, or inconsistent."""


class EmptyBm25IndexError(IndexingError):
    """Raised when a build or load would leave a queryable BM25 index with zero documents."""


class ReferenceIndexLoadError(IndexingError):
    """Raised by `indexing.references.load_index` when `references.json`
    exists but cannot be parsed as JSON or fails `FileReference`
    validation. Deliberately has no sibling `ReferenceIndexNotBuiltError`
    -- an absent reference index is a normal, lenient case (mirrors
    `manifest.load_manifest`'s `None`-on-absence convention), not an error
    condition; only corruption of a file that does exist is.
    """


__all__ = [
    "Bm25LoadError",
    "Bm25NotBuiltError",
    "EmbeddingModelError",
    "EmptyBm25IndexError",
    "EmptyIndexError",
    "IndexLoadError",
    "IndexNotBuiltError",
    "IndexingError",
    "ReferenceIndexLoadError",
]
