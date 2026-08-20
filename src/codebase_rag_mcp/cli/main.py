"""Command-line entrypoint for ``codebase-rag``.

Subcommands are intentionally minimal at the scaffold stage. Today the
CLI supports ``--version`` and ``serve`` so the package can be installed
and exercised end-to-end without any RAG pipeline code yet.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from codebase_rag_mcp import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebase-rag",
        description="Codebase RAG MCP server (scaffold stage).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version and exit.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "serve",
        help="Run the MCP server over stdio (stub: responds to list_tools with a 'ping' tool).",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch.

    Returns a process exit code (0 = success, non-zero = error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        sys.stdout.write(f"codebase-rag {__version__}\n")
        return 0

    if args.command == "serve":
        # Lazy import keeps the CLI snappy when only --version is requested.
        from codebase_rag_mcp.mcp import run

        run()
        return 0

    # No subcommand and no --version: print help and exit non-zero.
    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
