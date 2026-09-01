"""Repo-wide chunk collection: loop parse_file -> chunk_file over a RepoStats.

This is the piece explicitly deferred by the Day 03 spec: `parse_file`/
`chunk_file` each operate on one file at a time; nothing before this module
aggregates "all chunks for a repo" into a single collection.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL_NAME, INDEX_DIR
from codebase_rag_mcp.indexing import bm25, manifest, references, vector
from codebase_rag_mcp.indexing.cache import ChunkCache, ChunkCacheEntry, hash_bytes
from codebase_rag_mcp.indexing.models import (
    Bm25IndexStats,
    FileReadFailure,
    FileReference,
    IncrementalBuildStats,
    RepoChunkCollection,
    SkippedChunk,
    VectorIndexStats,
)
from codebase_rag_mcp.ingestion.models import RepoStats
from codebase_rag_mcp.ingestion.scanner import scan
from codebase_rag_mcp.parser.exceptions import ParseError, UnsupportedLanguageError
from codebase_rag_mcp.parser.extractor import parse_file

logger = logging.getLogger(__name__)


def _parse_and_chunk_source(
    logical_path: Path, language: str, source: bytes, *, repo: str = ""
) -> tuple[list[Chunk], list[FileReference]]:
    """Pure, I/O-free wrapper around `parse_file` + `chunk_file` + the
    `FileReference`-tagging `collect_repo_chunks` already did inline.

    Shared by `collect_repo_chunks` (unchanged external behavior) and
    `build_all_indexes_incremental`'s cache-miss path, so both go through
    exactly one parse/chunk implementation. Raises
    `UnsupportedLanguageError`/`ParseError` exactly as `parse_file` itself
    does -- each caller wraps this in its own identical try/except,
    recording a `FileReadFailure`.
    """
    parse_result = parse_file(logical_path, language, source)
    chunks = chunk_file(parse_result, source, repo=repo)
    file_references = [
        FileReference(
            file=parse_result.path, name=r.name, kind=r.kind, line=r.line, module=r.module
        )
        for r in parse_result.references
    ]
    return chunks, file_references


def collect_repo_chunks(root: Path, stats: RepoStats, *, repo: str = "") -> RepoChunkCollection:
    """Loop parse_file -> chunk_file over every included file in `stats`.

    `root` is the physical checkout directory (`RepoSource.root`);
    `stats.files[i].path` is repo-relative, so each file is read from
    `root / file_record.path`. Only `included=True` records are processed.

    Two distinct failure modes are recorded (path + reason) rather than
    raised, and never abort the loop:
      - the file cannot be *read* (deleted, permission-denied, or otherwise
        gone between `scan()` and this call);
      - the file's language has no configured Tree-sitter grammar, or
        Tree-sitter cannot produce a tree at all
        (`UnsupportedLanguageError`/`ParseError`) -- ingestion's
        `included=True` covers many more languages (markdown, yaml, go,
        rust, ...) than `parser.grammars.LANGUAGE_TO_GRAMMAR` currently
        supports, so this is not a rare edge case: any real repo with a
        README hits it.

    `chunk_file` itself never raises (it decodes with `errors="replace"`),
    so only the read and the `parse_file` call are wrapped.
    """
    chunks: list[Chunk] = []
    failures: list[FileReadFailure] = []
    file_references: list[FileReference] = []

    for record in stats.files:
        if not record.included:
            continue

        physical_path = root / record.path
        logical_path = Path(record.path)  # repo-relative; drives .tsx routing + Chunk.file

        try:
            source = physical_path.read_bytes()
        except OSError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append(FileReadFailure(path=record.path, reason=reason))
            logger.warning("could not read %s: %s", record.path, reason)
            continue

        try:
            file_chunks, file_refs = _parse_and_chunk_source(
                logical_path, record.language, source, repo=repo
            )
        except (UnsupportedLanguageError, ParseError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append(FileReadFailure(path=record.path, reason=reason))
            logger.warning("could not parse %s: %s", record.path, reason)
            continue

        chunks.extend(file_chunks)
        file_references.extend(file_refs)

    return RepoChunkCollection(chunks=chunks, read_failures=failures, references=file_references)


def build_all_indexes(
    root: Path,
    *,
    index_dir: str | Path = INDEX_DIR,
    vector_model_name: str = EMBEDDING_MODEL_NAME,
    vector_batch_size: int = EMBEDDING_BATCH_SIZE,
    repo: str = "",
) -> tuple[VectorIndexStats, Bm25IndexStats]:
    """Scan `root`, collect its chunks exactly once, build both the vector
    and BM25 indexes from that single `list[Chunk]`.

    This is the structural enforcement of "same chunk set, same `Chunk.id`
    values on both indexes": `collect_repo_chunks` is called exactly once
    here, so there is no path in this codebase where the two index builds
    can independently observe a repo that changed between them.

    Also builds+persists the repo-wide reference/import index
    (`indexing.references.build_index`/`write_index`, Day 10) from the
    same `result.references` -- a side effect, like the manifest write
    below, not part of the return tuple, so this function's return shape
    stays unchanged for existing callers.

    Also writes `indexing.manifest.write_manifest(index_dir, repo_root=root,
    source=repo)` immediately after both indexes build successfully --
    `repo` (the raw ingestion source string, e.g. a GitHub URL or local
    path) is reused as the manifest's `source` rather than adding a second,
    possibly-diverging parameter. This is the one piece of state
    `mcp.server`'s `get_file_context` needs to resolve a repo-relative
    `Chunk.file` back to an absolute path in a process started fresh,
    possibly long after this call returns (see DECISIONS.md D-023).
    """
    file_stats = scan(root)
    result = collect_repo_chunks(root, file_stats, repo=repo)
    vector_stats = vector.build_index(
        result.chunks,
        index_dir=index_dir,
        model_name=vector_model_name,
        batch_size=vector_batch_size,
    )
    bm25_stats = bm25.build_index(result.chunks, index_dir=index_dir)
    reference_index = references.build_index(result.references)
    references.write_index(reference_index, index_dir=index_dir)
    manifest.write_manifest(index_dir, repo_root=root, source=repo)
    return vector_stats, bm25_stats


def build_all_indexes_incremental(
    root: Path,
    *,
    index_dir: str | Path = INDEX_DIR,
    vector_model_name: str = EMBEDDING_MODEL_NAME,
    vector_batch_size: int = EMBEDDING_BATCH_SIZE,
    repo: str = "",
    force: bool = False,
) -> IncrementalBuildStats:
    """Cache-aware sibling of `build_all_indexes`: skip re-parsing/
    re-chunking/re-embedding any included file whose content hash matches
    a cached entry from a prior run, while always fully rebuilding the
    FAISS/BM25/reference structures from the complete current
    chunks/embeddings/references set every run -- BM25's IDF weighting is
    a whole-corpus quantity a partial update would silently corrupt.

    `build_all_indexes` itself is left completely unchanged (same
    signature, same behavior, same tests) -- this is an additive entry
    point `cli/main.py`'s `index` subcommand calls instead; see
    DECISIONS.md for why the two are kept separate rather than merging
    caching into `build_all_indexes` in place.

    Every cache-miss file's chunks are batched into exactly ONE
    `vector.embed_chunks` call (never one call per file), preserving the
    existing whole-corpus-batched-call efficiency decision (D-017).

    A cache entry for a file no longer present in this run's
    `included=True` set (deleted, renamed, or newly excluded by a filter
    rule) is dropped before the cache is persisted -- every run, whether
    or not `force` was used -- so deletions/renames/newly-filtered files
    always take effect on the very next reindex rather than leaving a
    ghost entry.

    `force=True` bypasses the cache entirely (every file is treated as a
    miss) without deleting the cache file on disk, so a forced run also
    naturally repopulates/corrects the cache for the next incremental run.
    """
    file_stats = scan(root)
    cache = ChunkCache() if force else ChunkCache.load(index_dir)

    included_paths: set[str] = set()
    fresh_chunks_flat: list[Chunk] = []
    fresh_pending: dict[str, tuple[list[Chunk], list[FileReference], str]] = {}
    hit_count = 0
    miss_count = 0

    for record in file_stats.files:
        if not record.included:
            continue
        included_paths.add(record.path)

        physical_path = root / record.path
        try:
            source = physical_path.read_bytes()
        except OSError as exc:
            logger.warning("could not read %s: %s", record.path, f"{type(exc).__name__}: {exc}")
            continue

        content_hash = hash_bytes(source)
        cached_entry = None if force else cache.get(record.path, content_hash)
        if cached_entry is not None:
            hit_count += 1
            continue

        miss_count += 1
        try:
            file_chunks, file_references = _parse_and_chunk_source(
                Path(record.path), record.language, source, repo=repo
            )
        except (UnsupportedLanguageError, ParseError) as exc:
            logger.warning("could not parse %s: %s", record.path, f"{type(exc).__name__}: {exc}")
            continue

        fresh_chunks_flat.extend(file_chunks)
        fresh_pending[record.path] = (file_chunks, file_references, content_hash)

    # Exactly ONE embed_chunks call over every cache-miss file's chunks
    # combined -- D-017's batching-efficiency decision preserved, never
    # one call per file.
    if fresh_chunks_flat:
        fresh_embedded, fresh_vectors, _fresh_skipped = vector.embed_chunks(
            fresh_chunks_flat, model_name=vector_model_name, batch_size=vector_batch_size
        )
    else:
        fresh_embedded, fresh_vectors = [], np.empty((0, 0), dtype="float32")

    fresh_vector_by_id = {
        chunk.id: vec.tolist() for chunk, vec in zip(fresh_embedded, fresh_vectors, strict=True)
    }

    for path, (file_chunks, file_references, content_hash) in fresh_pending.items():
        embeddings_for_file = {
            chunk.id: fresh_vector_by_id[chunk.id]
            for chunk in file_chunks
            if chunk.id in fresh_vector_by_id
        }
        cache.put(
            ChunkCacheEntry(
                file=path,
                content_hash=content_hash,
                chunks=file_chunks,
                embeddings=embeddings_for_file,
                references=file_references,
            )
        )

    deleted_count = cache.retain_only(included_paths)
    cache.write(index_dir)

    # The cache, post-retain_only, is now the single source of truth for
    # BOTH BM25 (needs the full corpus) and FAISS (needs only chunks with
    # a vector) -- derived fresh from it rather than re-tracked separately
    # during the loop above.
    full_corpus_chunks: list[Chunk] = []
    full_corpus_references: list[FileReference] = []
    faiss_chunks: list[Chunk] = []
    faiss_vector_rows: list[list[float]] = []
    skipped_total: list[SkippedChunk] = []
    for entry in cache.entries():
        full_corpus_chunks.extend(entry.chunks)
        full_corpus_references.extend(entry.references)
        for chunk in entry.chunks:
            vec = entry.embeddings.get(chunk.id)
            if vec is not None:
                faiss_chunks.append(chunk)
                faiss_vector_rows.append(vec)
            else:
                skipped_total.append(SkippedChunk(chunk_id=chunk.id, reason="empty content"))

    vector_stats = vector.build_index_from_embeddings(
        faiss_chunks,
        np.asarray(faiss_vector_rows, dtype="float32")
        if faiss_vector_rows
        else np.empty((0, 0), dtype="float32"),
        skipped_total,
        chunks_requested=len(full_corpus_chunks),
        index_dir=index_dir,
    )
    bm25_stats = bm25.build_index(full_corpus_chunks, index_dir=index_dir)
    reference_index = references.build_index(full_corpus_references)
    references.write_index(reference_index, index_dir=index_dir)
    manifest.write_manifest(index_dir, repo_root=root, source=repo)

    return IncrementalBuildStats(
        vector_stats=vector_stats,
        bm25_stats=bm25_stats,
        files_total=hit_count + miss_count,
        files_cache_hit=hit_count,
        files_cache_miss=miss_count,
        files_deleted=deleted_count,
        force_used=force,
    )


__all__ = ["build_all_indexes", "build_all_indexes_incremental", "collect_repo_chunks"]
