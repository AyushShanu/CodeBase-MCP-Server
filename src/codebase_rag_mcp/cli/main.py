"""Command-line entrypoint for ``codebase-rag``.

``index`` was the bare Day 08 enabler making ``serve``'s tools runnable
against a real repo end-to-end; Day 11 adds the incremental-indexing
``--force`` flag and its skip/reindex/delete count reporting -- the CLI
polish CLAUDE.md scoped to this day.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from codebase_rag_mcp import __version__
from codebase_rag_mcp.config import INDEX_DIR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebase-rag",
        description="Codebase RAG MCP server.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version and exit.",
    )
    sub = parser.add_subparsers(dest="command")

    index_parser = sub.add_parser(
        "index",
        help="Clone/scan a repository and build its vector + BM25 indexes.",
    )
    index_parser.add_argument(
        "source",
        help="An https:// git URL or a local filesystem path to index.",
    )
    index_parser.add_argument(
        "--index-dir",
        default=INDEX_DIR,
        help=f"Directory to write the index into (default: {INDEX_DIR}).",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the incremental chunk cache; fully re-parse and re-embed every file.",
    )

    sub.add_parser(
        "serve",
        help=(
            "Run the MCP server (search_code, find_symbol, get_file_context, ask, "
            "analyze_impact, repository_summary) over stdio."
        ),
    )

    return parser


def _run_index(source: str, index_dir: str, *, force: bool = False) -> int:
    """Ingest `source` and incrementally build its vector + BM25 indexes
    under `index_dir`, reusing cached parse/chunk/embed output for any
    file whose content hasn't changed since the last run.

    Lazy-imports `ingestion`/`indexing` so `--version`/`--help` stay fast.
    Prints basic stats, the per-file cache accounting, and the manifest's
    `repo_root` on success. `force=True` bypasses the cache entirely.
    """
    from codebase_rag_mcp.indexing.repo import build_all_indexes_incremental
    from codebase_rag_mcp.ingestion.loader import load_repo

    repo_source = load_repo(source)
    stats = build_all_indexes_incremental(
        repo_source.root, index_dir=index_dir, repo=source, force=force
    )
    sys.stdout.write(
        f"Indexed {source!r} -> {index_dir}\n"
        f"  checkout root: {repo_source.root}\n"
        f"  files: {stats.files_total} total, {stats.files_cache_hit} cached (skipped), "
        f"{stats.files_cache_miss} reparsed/reembedded, {stats.files_deleted} removed"
        f"{' (--force: cache bypassed)' if stats.force_used else ''}\n"
        f"  vector: {stats.vector_stats.chunks_embedded} embedded, "
        f"{stats.vector_stats.chunks_skipped} skipped (dim={stats.vector_stats.embedding_dimension})\n"
        f"  bm25:   {stats.bm25_stats.chunks_indexed} indexed, "
        f"{stats.bm25_stats.chunks_skipped} skipped (vocab={stats.bm25_stats.vocabulary_size})\n"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch.

    Returns a process exit code (0 = success, non-zero = error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        sys.stdout.write(f"codebase-rag {__version__}\n")
        return 0

    if args.command == "index":
        return _run_index(args.source, args.index_dir, force=args.force)

    if args.command == "serve":
        # Lazy import keeps the CLI snappy when only --version is requested.
        # Imports mcp.server directly (not the codebase_rag_mcp.mcp package)
        # to avoid the package __init__ eagerly loading mcp.server -- see
        # mcp/__init__.py's own docstring for why (Day 10 impact.models ->
        # mcp.models cross-import would otherwise cycle through it).
        from codebase_rag_mcp.mcp.server import run

        run()
        return 0

    # No subcommand and no --version: print help and exit non-zero.
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
