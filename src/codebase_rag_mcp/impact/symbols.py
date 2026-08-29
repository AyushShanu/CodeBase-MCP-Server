"""Shared symbol-definition matching, extracted from the pre-Day-10
`mcp.server._match_symbol` so `find_symbol` and `analyze_impact` share
exactly one implementation -- never a second, possibly-diverging one.
"""

from __future__ import annotations

import re

from codebase_rag_mcp.chunker.models import Chunk

_PART_SUFFIX_RE = re.compile(r"#part\d+$")


def strip_part_suffix(symbol: str) -> str:
    """Strip a `chunker.fallback.split_oversized_symbol`-applied `#partN`
    suffix (1-indexed, no zero-padding), or return `symbol` unchanged.
    Never leaks a raw `#partN`-suffixed chunk name into an externally
    facing report.
    """
    return _PART_SUFFIX_RE.sub("", symbol)


def bare_trailing_name(symbol: str) -> str:
    """`strip_part_suffix` first, then the part after the last "." if
    any (a qualified `ClassName.method` name's own trailing component).
    """
    return strip_part_suffix(symbol).rsplit(".", 1)[-1]


def match_symbol_chunks(symbol: str, chunks: list[Chunk]) -> tuple[list[Chunk], list[Chunk]]:
    """Exact + qualified-suffix symbol-definition lookup over `chunks`.

    Returns `(exact, suffix)`, each separately sorted by `(file,
    start_line)` -- identical matching semantics to the pre-extraction
    `mcp.server._match_symbol` (`chunk.symbol == symbol` is exact;
    `chunk.symbol.endswith(f".{symbol}")` is suffix).
    """
    exact: list[Chunk] = []
    suffix: list[Chunk] = []
    suffix_needle = f".{symbol}"
    for chunk in chunks:
        if chunk.symbol == symbol:
            exact.append(chunk)
        elif chunk.symbol.endswith(suffix_needle):
            suffix.append(chunk)
    exact.sort(key=lambda c: (c.file, c.start_line))
    suffix.sort(key=lambda c: (c.file, c.start_line))
    return exact, suffix


def count_distinct_definitions(bare_name: str, chunks: list[Chunk]) -> int:
    """How many distinct (file, `#partN`-unsuffixed-qualified-symbol)
    definitions, repo-wide, share `bare_name` as their own bare trailing
    component.

    Counts DEFINITIONS, not chunks -- two chunks from the same
    oversized-symbol split (`Foo.bar#part1`/`Foo.bar#part2`) count once.
    Whole-file-fallback chunks (`symbol == ""`) are excluded -- never
    definitions. Used by `impact.analyzer.analyze_impact` to decide
    CONFIRMED (`<= 1`) vs LIKELY (`> 1`).
    """
    distinct: set[tuple[str, str]] = set()
    for chunk in chunks:
        if not chunk.symbol:
            continue
        if bare_trailing_name(chunk.symbol) == bare_name:
            distinct.add((chunk.file, strip_part_suffix(chunk.symbol)))
    return len(distinct)


__all__ = [
    "bare_trailing_name",
    "count_distinct_definitions",
    "match_symbol_chunks",
    "strip_part_suffix",
]
