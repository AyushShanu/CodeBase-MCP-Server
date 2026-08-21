"""Chunker: turn parsed AST symbols into retrievable code chunks.

Builds one `Chunk` per `ParsedSymbol` from a `ParseResult` (splitting
oversized symbols along in-span line boundaries via `fallback`), with
metadata attachment (file, language, symbol kind, line range) and
deterministic IDs for stable citations. Operates on one file at a time;
repo-wide orchestration is out of scope here (see Day 04).
"""

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.chunker.fallback import (
    DEFAULT_MAX_CHUNK_LINES,
    SplitSpan,
    split_oversized_symbol,
)
from codebase_rag_mcp.chunker.models import Chunk

__all__ = [
    "DEFAULT_MAX_CHUNK_LINES",
    "Chunk",
    "SplitSpan",
    "chunk_file",
    "split_oversized_symbol",
]
