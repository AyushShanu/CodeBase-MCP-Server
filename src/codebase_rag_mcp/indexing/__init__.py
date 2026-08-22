"""Indexing: dense vector index (FAISS) and repo-wide chunk collection.

`indexing.repo.collect_repo_chunks` loops `parser.parse_file` ->
`chunker.chunk_file` over every included file in a `RepoStats`, producing
one aggregated `list[Chunk]` for a whole repository. `indexing.vector`
embeds those chunks locally (`all-MiniLM-L6-v2` by default, see
`config.EMBEDDING_MODEL_NAME`) and stores them in a hand-rolled, persistent
FAISS `IndexFlatIP` index over L2-normalized vectors, alongside a parallel
chunk-metadata store keyed by FAISS vector ID.

`indexing.bm25` (sparse BM25 retrieval) remains an unimplemented
placeholder until Day 05 and has no exports here yet.
"""

from __future__ import annotations

from codebase_rag_mcp.indexing.exceptions import (
    EmbeddingModelError,
    EmptyIndexError,
    IndexingError,
    IndexLoadError,
    IndexNotBuiltError,
)
from codebase_rag_mcp.indexing.models import (
    FileReadFailure,
    IndexedChunk,
    RepoChunkCollection,
    SkippedChunk,
    VectorIndexStats,
    VectorQueryResult,
)
from codebase_rag_mcp.indexing.repo import collect_repo_chunks
from codebase_rag_mcp.indexing.vector import (
    VectorIndex,
    build_index,
    embed_chunks,
    embed_texts,
    load_index,
)

__all__ = [
    "EmbeddingModelError",
    "EmptyIndexError",
    "FileReadFailure",
    "IndexLoadError",
    "IndexNotBuiltError",
    "IndexedChunk",
    "IndexingError",
    "RepoChunkCollection",
    "SkippedChunk",
    "VectorIndex",
    "VectorIndexStats",
    "VectorQueryResult",
    "build_index",
    "collect_repo_chunks",
    "embed_chunks",
    "embed_texts",
    "load_index",
]
