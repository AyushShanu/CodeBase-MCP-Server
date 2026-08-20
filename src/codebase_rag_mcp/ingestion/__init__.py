"""Ingestion: obtain and scan a repository (GitHub URL or local path).

`ingest(source)` is the single public entry point for callers that just
want a `RepoStats` summary: it resolves `source` via `loader.load_repo`
(cloning an https:// URL or validating a local directory) and walks the
result via `scanner.scan`, applying `filters.py`'s ignore rules and
`languages.py`'s extension mapping. Callers needing finer control
(e.g. explicit cleanup of a temporary clone) should call `load_repo`
and `scan` directly.
"""

from __future__ import annotations

from codebase_rag_mcp.ingestion.exceptions import (
    IngestionError,
    InvalidRepoURLError,
    LocalPathError,
    LocalPathNotADirectoryError,
    LocalPathNotFoundError,
    RepoCloneError,
    RepoCloneTimeoutError,
)
from codebase_rag_mcp.ingestion.loader import load_repo
from codebase_rag_mcp.ingestion.models import (
    FileRecord,
    RepoSource,
    RepoSourceType,
    RepoStats,
)
from codebase_rag_mcp.ingestion.scanner import scan


def ingest(
    source: str,
    *,
    clone_timeout: float | None = None,
    max_file_size_bytes: int | None = None,
) -> RepoStats:
    """Resolve `source` and return a `RepoStats` summary of its contents.

    Cloned checkouts are left on disk under `DATA_DIR/clones/` -- this
    function does not clean up after itself. Use `load_repo` directly
    and call `RepoSource.cleanup()` if you need the clone removed.
    """
    load_kwargs: dict[str, float] = (
        {} if clone_timeout is None else {"clone_timeout": clone_timeout}
    )
    scan_kwargs: dict[str, int] = (
        {} if max_file_size_bytes is None else {"max_file_size_bytes": max_file_size_bytes}
    )
    repo_source = load_repo(source, **load_kwargs)
    return scan(repo_source.root, **scan_kwargs)


__all__ = [
    "FileRecord",
    "IngestionError",
    "InvalidRepoURLError",
    "LocalPathError",
    "LocalPathNotADirectoryError",
    "LocalPathNotFoundError",
    "RepoCloneError",
    "RepoCloneTimeoutError",
    "RepoSource",
    "RepoSourceType",
    "RepoStats",
    "ingest",
    "load_repo",
    "scan",
]
