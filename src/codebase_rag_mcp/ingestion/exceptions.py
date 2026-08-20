"""Typed exceptions for the ingestion pipeline.

Every error that `loader.py` can raise is a subclass of `IngestionError`
so callers can catch broadly (`except IngestionError`) or narrowly
(`except RepoCloneTimeoutError`) as needed.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion-related errors."""


class InvalidRepoURLError(IngestionError):
    """Raised when a repo URL uses a disallowed (non-https) scheme."""


class RepoCloneError(IngestionError):
    """Raised when `git clone` fails for a reason other than a timeout."""


class RepoCloneTimeoutError(RepoCloneError):
    """Raised when `git clone` exceeds the configured timeout.

    Subclasses `RepoCloneError` (a timeout is one kind of clone failure)
    while remaining independently catchable.
    """


class LocalPathError(IngestionError):
    """Base class for local-path validation errors."""


class LocalPathNotFoundError(LocalPathError):
    """Raised when a local-path source does not exist on disk."""


class LocalPathNotADirectoryError(LocalPathError):
    """Raised when a local-path source exists but is not a directory."""


__all__ = [
    "IngestionError",
    "InvalidRepoURLError",
    "LocalPathError",
    "LocalPathNotADirectoryError",
    "LocalPathNotFoundError",
    "RepoCloneError",
    "RepoCloneTimeoutError",
]
