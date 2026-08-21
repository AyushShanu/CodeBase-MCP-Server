"""Safe fallback splitting for oversized symbols.

Pure line-arithmetic over an already-extracted `ParsedSymbol` -- this
module never touches file bytes/text, keeping `chunker.chunk_file` the
single point in the pipeline where file bytes become text (see its
module docstring). When a symbol's line span exceeds `max_chunk_lines`,
it is split along line boundaries strictly *within* that symbol's own
span -- never across another symbol's boundary, never falling back to
raw character-count splitting of arbitrary content, per CLAUDE.md's
"AST-aware chunking only" rule. See DECISIONS.md for the
`DEFAULT_MAX_CHUNK_LINES` threshold and reasoning.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from codebase_rag_mcp.parser.models import ParsedSymbol

DEFAULT_MAX_CHUNK_LINES: Final[int] = 100


class SplitSpan(NamedTuple):
    """One line-range slice of a (possibly split) symbol."""

    name: str
    start_line: int
    end_line: int


def split_oversized_symbol(
    symbol: ParsedSymbol, *, max_chunk_lines: int = DEFAULT_MAX_CHUNK_LINES
) -> list[SplitSpan]:
    """Split `symbol` into one or more in-span `SplitSpan`s.

    Returns a single unsuffixed `SplitSpan` (matching `symbol.name` exactly)
    when the symbol's span is within `max_chunk_lines`. Otherwise returns
    multiple contiguous, non-overlapping spans -- each still within
    `[symbol.start_line, symbol.end_line]` -- suffixed `#part1`, `#part2`,
    etc. on the symbol's (possibly already-qualified) name.
    """
    total_lines = symbol.end_line - symbol.start_line + 1
    if total_lines <= max_chunk_lines:
        return [SplitSpan(symbol.name, symbol.start_line, symbol.end_line)]

    spans: list[SplitSpan] = []
    part = 1
    cursor = symbol.start_line
    while cursor <= symbol.end_line:
        end = min(cursor + max_chunk_lines - 1, symbol.end_line)
        spans.append(SplitSpan(f"{symbol.name}#part{part}", cursor, end))
        cursor = end + 1
        part += 1
    return spans


__all__ = ["DEFAULT_MAX_CHUNK_LINES", "SplitSpan", "split_oversized_symbol"]
