"""Pydantic models for the indexing stage's inputs and outputs.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `parser.models`/`chunker.models`/
`ingestion.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from codebase_rag_mcp.chunker.models import Chunk


class FileReadFailure(BaseModel):
    """One included file that produced zero chunks during repo-wide
    orchestration, and why.

    Covers both literal read failures (the file was deleted or became
    permission-denied between `scan()` and `indexing.repo.collect_repo_chunks`
    running) and a file whose language has no configured Tree-sitter grammar,
    or that Tree-sitter could not parse at all -- both are "this included
    file produced no chunks" outcomes and are recorded uniformly rather than
    aborting the whole-repo loop. `reason` is prefixed with the originating
    exception type name for debuggability.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str


class RepoChunkCollection(BaseModel):
    """Aggregated result of looping parse_file/chunk_file over a RepoStats."""

    model_config = ConfigDict(frozen=True)

    chunks: list[Chunk] = Field(default_factory=list)
    read_failures: list[FileReadFailure] = Field(default_factory=list)


class SkippedChunk(BaseModel):
    """One chunk that was not embedded/indexed, and why."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    reason: str


class IndexedChunk(BaseModel):
    """A `Chunk` plus the integer FAISS vector ID its embedding occupies."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    vector_id: int


class VectorIndexStats(BaseModel):
    """Counts returned from a `build_index` call, for CLI/logging visibility."""

    model_config = ConfigDict(frozen=True)

    chunks_requested: int
    chunks_embedded: int
    chunks_skipped: int
    skipped: list[SkippedChunk] = Field(default_factory=list)
    embedding_dimension: int
    index_size: int


class VectorQueryResult(BaseModel):
    """One nearest-neighbor result from `VectorIndex.query`."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    score: float


__all__ = [
    "FileReadFailure",
    "IndexedChunk",
    "RepoChunkCollection",
    "SkippedChunk",
    "VectorIndexStats",
    "VectorQueryResult",
]
