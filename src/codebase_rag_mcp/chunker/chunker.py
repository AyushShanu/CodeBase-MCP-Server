"""Turn a `ParseResult` into retrievable `Chunk`s.

This module is the *sole* point in the pipeline where file bytes become
text. `parser.extractor` stays in bytes throughout (Tree-sitter's own
position data is byte-based); decoding to text is deferred to here, once,
so no other module risks decoding the same file twice with a different
policy.
"""

from __future__ import annotations

import hashlib

from codebase_rag_mcp.chunker.fallback import DEFAULT_MAX_CHUNK_LINES, split_oversized_symbol
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.parser.models import ParseResult, SymbolKind


def _chunk_id(file: str, symbol: str, start_line: int) -> str:
    # sha256 over a natural unique key (file + symbol + start_line),
    # truncated to 16 hex chars. Deterministic: same inputs -> same id,
    # no randomness, no dict-order dependency -- keeps citations stable
    # across repeated indexing runs on an unchanged file.
    key = f"{file}::{symbol}::{start_line}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _line_count(lines: list[str]) -> int:
    # `text.split("\n")` leaves a trailing "" element for a file ending in
    # "\n" -- drop it so the whole-file fallback's end_line reflects the
    # file's real line count.
    count = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    return max(count, 1)


def chunk_file(
    parse_result: ParseResult,
    source: bytes,
    *,
    encoding: str = "utf-8",
    repo: str = "",
    max_chunk_lines: int = DEFAULT_MAX_CHUNK_LINES,
) -> list[Chunk]:
    """Build one `Chunk` per extracted symbol (or a whole-file fallback).

    Decodes `source` exactly once with `errors="replace"` so a file
    containing invalid byte sequences (or a stray UTF-8 BOM) never crashes
    chunking -- the replacement character doesn't insert or remove
    newlines, so line-splitting afterward stays correct. Lines are split
    once via `text.split("\\n")` -- deliberately not `str.splitlines()`,
    which additionally breaks on `\\r`/`\\v`/`\\f`/U+2028/2029 and would
    desync the line array from Tree-sitter's own newline-based row
    counting.

    Files with zero extracted symbols still get a single whole-file
    fallback `Chunk` (`type=SymbolKind.module`, `symbol=""`) rather than
    being dropped, so nothing indexed later has a gap.
    """
    text = source.decode(encoding, errors="replace")
    lines = text.split("\n")

    if not parse_result.symbols:
        return [
            Chunk(
                id=_chunk_id(parse_result.path, "", 1),
                repo=repo,
                file=parse_result.path,
                symbol="",
                type=SymbolKind.MODULE,
                language=parse_result.language,
                start_line=1,
                end_line=_line_count(lines),
                content=text,
            )
        ]

    chunks: list[Chunk] = []
    for symbol in parse_result.symbols:
        for span in split_oversized_symbol(symbol, max_chunk_lines=max_chunk_lines):
            content = "\n".join(lines[span.start_line - 1 : span.end_line])
            chunks.append(
                Chunk(
                    id=_chunk_id(parse_result.path, span.name, span.start_line),
                    repo=repo,
                    file=parse_result.path,
                    symbol=span.name,
                    type=symbol.kind,
                    language=parse_result.language,
                    start_line=span.start_line,
                    end_line=span.end_line,
                    content=content,
                )
            )
    return chunks


__all__ = ["chunk_file"]
