"""Typed exceptions for the MCP server stage (mcp.server).

Every error `server.py` can raise is a subclass of `MCPServerError` so
callers can catch broadly (`except MCPServerError`) or narrowly
(`except PathOutsideRepoRootError`) as needed. Note that over the real
stdio transport, `mcp.server.mcpserver.MCPServer`'s own request-dispatch
handler already converts any plain exception raised inside a tool function
into a clean `CallToolResult(is_error=True, content=[...str(exc)...])` --
these types exist for precise `str()` messages and for tests to assert
against, not because callers need to catch them to avoid a crash.
"""

from __future__ import annotations


class MCPServerError(Exception):
    """Base class for all MCP-server-stage errors."""


class IndexNotAvailableError(MCPServerError):
    """Raised by the server's `lifespan` startup check when neither the
    vector nor the BM25 index exists under `index_dir` -- there is nothing
    any tool could query. Surfaced at server startup, not per-call, so a
    misconfigured server fails immediately and visibly rather than
    accepting connections and failing every tool call opaquely."""


class RepoRootUnknownError(MCPServerError):
    """Raised by `get_file_context` when no manifest was written for this
    index (an older index built before `indexing.manifest` existed, or one
    built without ever calling `indexing.repo.build_all_indexes`) --
    `get_file_context` has no absolute checkout root to resolve `file`
    against. Every other V1 tool works fine without a manifest; only this
    one tool is disabled by its absence."""


class PathOutsideRepoRootError(MCPServerError):
    """Raised by `get_file_context` when the requested `file`, once
    resolved against the manifest's `repo_root`, does not stay contained
    within `repo_root` -- including a resolved symlink target that escapes
    it. Mirrors `ingestion.scanner.scan`'s own resolve-then-`relative_to`
    containment discipline, applied fresh here since this is separate file
    I/O at query time, not a reuse of that already-tested code path."""


class InvalidLineRangeError(MCPServerError):
    """Raised by `get_file_context` for a `start_line` < 1, a `start_line`
    greater than `end_line`, or a `start_line` beyond the file's actual
    last line. An `end_line` beyond the file's last line is deliberately
    NOT an error -- it is clamped to the file's actual last line instead,
    a lenient case documented on the tool itself."""


__all__ = [
    "IndexNotAvailableError",
    "InvalidLineRangeError",
    "MCPServerError",
    "PathOutsideRepoRootError",
    "RepoRootUnknownError",
]
