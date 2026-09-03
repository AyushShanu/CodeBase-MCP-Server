"""Command-line entrypoint for ``codebase-rag``.

``index`` was the bare Day 08 enabler making ``serve``'s tools runnable
against a real repo end-to-end; Day 11 adds the incremental-indexing
``--force`` flag and its skip/reindex/delete count reporting -- the CLI
polish CLAUDE.md scoped to this day. Day 13
(13-cross-agent-mcp-packaging-portability) adds ``--env-file`` and the
explicit ``config._resolve_index_dir``/``_resolve_env_path`` resolution
calls in ``main()`` -- see DECISIONS.md D-027.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from codebase_rag_mcp import __version__, config
from codebase_rag_mcp.mcp.exceptions import InvalidIndexDirError


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
        default=None,
        help=(
            "Directory to write the index into (default: an OS-appropriate per-repo "
            "directory under the platformdirs user-data path, keyed by a hash of the "
            "resolved repo source -- the same repo always resolves to the same directory "
            "regardless of the current directory. An explicit value here must be "
            "absolute; a relative path is rejected outright, never silently resolved "
            "against the current directory)."
        ),
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the incremental chunk cache; fully re-parse and re-embed every file.",
    )

    serve_parser = sub.add_parser(
        "serve",
        help=(
            "Run the MCP server (search_code, find_symbol, get_file_context, ask, "
            "analyze_impact, repository_summary) over stdio."
        ),
    )
    serve_parser.add_argument(
        "--repo",
        default=None,
        help=(
            "An https:// git URL or local path to zero-config auto-index if no usable "
            "index exists yet under --index-dir (default: the REPO_SOURCE env var, or "
            "the current directory if it contains a .git directory)."
        ),
    )
    serve_parser.add_argument(
        "--index-dir",
        default=None,
        help=(
            "Directory to read/write the index from (default: an OS-appropriate per-repo "
            "directory under the platformdirs user-data path, keyed by a hash of the "
            "resolved repo source -- the same repo always resolves to the same directory "
            "regardless of the current directory or which MCP client launched this "
            "process. An explicit value here must be absolute; a relative path is "
            "rejected outright, never silently resolved against the current directory)."
        ),
    )
    serve_parser.add_argument(
        "--no-auto-index",
        action="store_true",
        help=(
            "Disable zero-config auto-indexing; require an index already built by "
            "'codebase-rag index' first."
        ),
    )
    serve_parser.add_argument(
        "--env-file",
        default=None,
        help=(
            "Explicit path to a .env file to load -- highest precedence, overriding the "
            "resolved repo-root .env and the current-directory .env fallback. A variable "
            "already set in the process environment -- e.g. by an MCP client's own "
            '"env" config block -- is never overridden by any .env file.'
        ),
    )

    return parser


def _run_index(source: str, index_dir: str | Path, *, force: bool = False) -> int:
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
        try:
            resolved_index_dir = config._resolve_index_dir(args.source, explicit=args.index_dir)
        except InvalidIndexDirError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        return _run_index(args.source, resolved_index_dir, force=args.force)

    if args.command == "serve":
        # Day 13: resolve the effective repo source, the index directory,
        # and which single .env file (if any) to load -- and reload
        # `config` -- BEFORE importing mcp.server below. Config's
        # provider-key/INDEX_DIR-style constants are `Final`, captured once
        # at each module's own import time; every indexing/generation
        # submodule captures its own such defaults the instant mcp.server
        # is first imported. Doing this resolution after that import would
        # be too late for any of it to take effect. See DECISIONS.md D-027.
        effective_source = config._resolve_effective_source(
            repo_flag=args.repo, repo_source_env=config.REPO_SOURCE, cwd=None
        )
        try:
            resolved_index_dir = config._resolve_index_dir(
                effective_source, explicit=args.index_dir
            )
        except InvalidIndexDirError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2

        env_path = config._resolve_env_path(effective_source or "", explicit=args.env_file)
        if env_path is not None and env_path != config._DOTENV_PATH:
            load_dotenv(env_path, override=False)
        importlib.reload(config)

        # Lazy import keeps the CLI snappy when only --version is requested,
        # and -- as of Day 13 -- ensures every transitively-imported
        # indexing/generation submodule's own def-time config defaults see
        # the fully-resolved environment from above. Imports mcp.server
        # directly (not the codebase_rag_mcp.mcp package) to avoid the
        # package __init__ eagerly loading mcp.server -- see
        # mcp/__init__.py's own docstring for why (Day 10 impact.models ->
        # mcp.models cross-import would otherwise cycle through it).
        from codebase_rag_mcp.mcp.server import run

        run(
            repo_source=effective_source,
            index_dir=resolved_index_dir,
            auto_index=not args.no_auto_index,
            env_path=env_path,
        )
        return 0

    # No subcommand and no --version: print help and exit non-zero.
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
