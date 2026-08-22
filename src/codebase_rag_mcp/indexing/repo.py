"""Repo-wide chunk collection: loop parse_file -> chunk_file over a RepoStats.

This is the piece explicitly deferred by the Day 03 spec: `parse_file`/
`chunk_file` each operate on one file at a time; nothing before this module
aggregates "all chunks for a repo" into a single collection.
"""

from __future__ import annotations

import logging
from pathlib import Path

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL_NAME, INDEX_DIR
from codebase_rag_mcp.indexing import bm25, vector
from codebase_rag_mcp.indexing.models import (
    Bm25IndexStats,
    FileReadFailure,
    RepoChunkCollection,
    VectorIndexStats,
)
from codebase_rag_mcp.ingestion.models import RepoStats
from codebase_rag_mcp.ingestion.scanner import scan
from codebase_rag_mcp.parser.exceptions import ParseError, UnsupportedLanguageError
from codebase_rag_mcp.parser.extractor import parse_file

logger = logging.getLogger(__name__)


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
            parse_result = parse_file(logical_path, record.language, source)
        except (UnsupportedLanguageError, ParseError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append(FileReadFailure(path=record.path, reason=reason))
            logger.warning("could not parse %s: %s", record.path, reason)
            continue

        chunks.extend(chunk_file(parse_result, source, repo=repo))

    return RepoChunkCollection(chunks=chunks, read_failures=failures)


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
    return vector_stats, bm25_stats


__all__ = ["build_all_indexes", "collect_repo_chunks"]
