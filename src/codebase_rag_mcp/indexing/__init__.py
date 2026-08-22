"""Indexing: dense vector index (FAISS) and repo-wide chunk collection.

`indexing.repo.collect_repo_chunks` loops `parser.parse_file` ->
`chunker.chunk_file` over every included file in a `RepoStats`, producing
one aggregated `list[Chunk]` for a whole repository. `indexing.vector`
embeds those chunks locally (`all-MiniLM-L6-v2` by default, see
`config.EMBEDDING_MODEL_NAME`) and stores them in a hand-rolled, persistent
FAISS `IndexFlatIP` index over L2-normalized vectors, alongside a parallel
chunk-metadata store keyed by FAISS vector ID.

`indexing.bm25` (sparse BM25 retrieval, Day 05) mirrors the same
build/persist/load/query shape over a `rank_bm25.BM25Okapi` corpus; its
`build_index`/`load_index`/`Bm25Index` names collide with `vector`'s, so
`indexing.bm25` is not re-exported here -- import it as
`from codebase_rag_mcp.indexing import bm25` instead.
`indexing.repo.build_all_indexes` builds both indexes from one chunk
collection pass and is re-exported below.
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
from codebase_rag_mcp.indexing.repo import build_all_indexes, collect_repo_chunks
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
    "build_all_indexes",
    "build_index",
    "collect_repo_chunks",
    "embed_chunks",
    "embed_texts",
    "load_index",
]
