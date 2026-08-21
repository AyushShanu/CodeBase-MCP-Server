"""Tests for the chunking stage: oversized-symbol fallback splitting and
`chunk_file` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.chunker.fallback import SplitSpan, split_oversized_symbol
from codebase_rag_mcp.parser.extractor import parse_file
from codebase_rag_mcp.parser.models import ParsedSymbol, ParseResult, SymbolKind

# --- fallback: pure line-splitting -------------------------------------------- #


def test_split_oversized_symbol_within_budget_is_unsplit() -> None:
    symbol = ParsedSymbol(
        name="foo", kind=SymbolKind.FUNCTION, start_line=1, end_line=10, start_byte=0, end_byte=1
    )

    spans = split_oversized_symbol(symbol, max_chunk_lines=10)

    assert spans == [SplitSpan("foo", 1, 10)]


def test_split_oversized_symbol_splits_along_line_boundaries() -> None:
    symbol = ParsedSymbol(
        name="foo", kind=SymbolKind.FUNCTION, start_line=1, end_line=25, start_byte=0, end_byte=1
    )

    spans = split_oversized_symbol(symbol, max_chunk_lines=10)

    assert [s.name for s in spans] == ["foo#part1", "foo#part2", "foo#part3"]
    # Contiguous, non-overlapping, and fully within the original span.
    assert spans[0].start_line == 1 and spans[0].end_line == 10
    assert spans[1].start_line == 11 and spans[1].end_line == 20
    assert spans[2].start_line == 21 and spans[2].end_line == 25
    for span in spans:
        assert 1 <= span.start_line <= span.end_line <= 25


@pytest.mark.parametrize("base_name", ["handleRequest", "Button.render"])
def test_split_oversized_symbol_suffix_applied_after_full_base_name(base_name: str) -> None:
    symbol = ParsedSymbol(
        name=base_name,
        kind=SymbolKind.METHOD,
        start_line=1,
        end_line=15,
        start_byte=0,
        end_byte=1,
    )

    spans = split_oversized_symbol(symbol, max_chunk_lines=10)

    assert spans[0].name == f"{base_name}#part1"
    assert spans[1].name == f"{base_name}#part2"


# --- chunk_file: one chunk per symbol, content matches raw slice ------------- #

_TS_FIXTURE = b"""function foo() {
  return 1;
}

class Widget {
  render() {
    return null;
  }
}

interface Props {
  name: string;
}
"""


def test_chunk_file_content_matches_independent_slice_of_fixture() -> None:
    parse_result = parse_file(Path("x.ts"), "typescript", _TS_FIXTURE)

    chunks = chunk_file(parse_result, _TS_FIXTURE)

    fixture_lines = _TS_FIXTURE.decode("utf-8").split("\n")
    assert len(chunks) == len(parse_result.symbols)
    for chunk in chunks:
        expected = "\n".join(fixture_lines[chunk.start_line - 1 : chunk.end_line])
        assert chunk.content == expected


# --- chunk_file: whole-file fallback ------------------------------------------ #


def test_chunk_file_zero_symbols_returns_module_fallback_chunk() -> None:
    parse_result = ParseResult(path="empty.ts", language="typescript", symbols=[])
    source = b"const x = 1;\nconst y = 2;\n"

    chunks = chunk_file(parse_result, source)

    assert len(chunks) == 1
    assert chunks[0].type == SymbolKind.MODULE
    assert chunks[0].symbol == ""
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2
    assert chunks[0].content == source.decode("utf-8")


# --- chunk_file: decode safety ------------------------------------------------ #


def test_chunk_file_invalid_utf8_bytes_does_not_raise() -> None:
    symbol = ParsedSymbol(
        name="foo", kind=SymbolKind.FUNCTION, start_line=1, end_line=1, start_byte=0, end_byte=1
    )
    parse_result = ParseResult(path="bad.ts", language="typescript", symbols=[symbol])
    source = b"function foo() {}\n\xff\xfe garbage\n"

    chunks = chunk_file(parse_result, source)

    assert len(chunks) == 1
    assert chunks[0].content == "function foo() {}"


def test_chunk_file_utf8_bom_does_not_raise() -> None:
    parse_result = ParseResult(path="bom.ts", language="typescript", symbols=[])
    source = b"\xef\xbb\xbfconst x = 1;\n"

    chunks = chunk_file(parse_result, source)

    assert len(chunks) == 1
    assert "const x = 1;" in chunks[0].content


# --- chunk_file: oversized symbol fallback wiring ----------------------------- #


def test_chunk_file_oversized_symbol_produces_suffixed_parts_within_span() -> None:
    lines = [f"line {i}" for i in range(1, 26)]  # 25 lines
    source = ("\n".join(lines) + "\n").encode("utf-8")
    symbol = ParsedSymbol(
        name="bigFunc",
        kind=SymbolKind.FUNCTION,
        start_line=1,
        end_line=25,
        start_byte=0,
        end_byte=1,
    )
    parse_result = ParseResult(path="big.ts", language="typescript", symbols=[symbol])

    chunks = chunk_file(parse_result, source, max_chunk_lines=10)

    assert [c.symbol for c in chunks] == ["bigFunc#part1", "bigFunc#part2", "bigFunc#part3"]
    for chunk in chunks:
        assert 1 <= chunk.start_line <= chunk.end_line <= 25
        expected = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
        assert chunk.content == expected


# --- chunk_file: deterministic ids -------------------------------------------- #


def test_chunk_file_ids_deterministic_across_repeated_runs() -> None:
    parse_result_a = parse_file(Path("x.ts"), "typescript", _TS_FIXTURE)
    parse_result_b = parse_file(Path("x.ts"), "typescript", _TS_FIXTURE)

    chunks_a = chunk_file(parse_result_a, _TS_FIXTURE)
    chunks_b = chunk_file(parse_result_b, _TS_FIXTURE)

    assert [c.id for c in chunks_a] == [c.id for c in chunks_b]


def test_chunk_file_ids_distinct_within_a_file() -> None:
    parse_result = parse_file(Path("x.ts"), "typescript", _TS_FIXTURE)

    chunks = chunk_file(parse_result, _TS_FIXTURE)

    assert len({c.id for c in chunks}) == len(chunks)
