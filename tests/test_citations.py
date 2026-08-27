"""Tests for deterministic citation attachment/formatting (citations.attach,
citations.format).
"""

from __future__ import annotations

import logging

import pytest

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.citations.attach import attach_citations
from codebase_rag_mcp.citations.format import format_citations_markdown
from codebase_rag_mcp.citations.models import Citation
from codebase_rag_mcp.parser.models import SymbolKind
from codebase_rag_mcp.reranker.models import RerankedResult
from codebase_rag_mcp.retrieval.models import HybridQueryResult


def _chunk(
    chunk_id: str,
    *,
    file: str = "a.py",
    symbol: str = "foo",
    start_line: int = 1,
    end_line: int = 2,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        repo="",
        file=file,
        symbol=symbol,
        type=SymbolKind.FUNCTION,
        language="python",
        start_line=start_line,
        end_line=end_line,
        content="def foo():\n    return 1\n",
    )


def _reranked_result(
    chunk_id: str,
    *,
    file: str = "a.py",
    symbol: str = "foo",
    start_line: int = 1,
    end_line: int = 2,
    rerank_score: float = 0.5,
    rerank_rank: int = 1,
) -> RerankedResult:
    chunk = _chunk(chunk_id, file=file, symbol=symbol, start_line=start_line, end_line=end_line)
    hybrid_result = HybridQueryResult(chunk=chunk, score=0.01)
    return RerankedResult(
        hybrid_result=hybrid_result, rerank_score=rerank_score, rerank_rank=rerank_rank
    )


# --- attach_citations: mapping / fidelity --------------------------------------- #


def test_attach_citations_maps_known_ids_to_chunk_metadata_in_given_order() -> None:
    a = _reranked_result("a", file="a.py", symbol="foo", start_line=1, end_line=5)
    b = _reranked_result("b", file="b.py", symbol="bar", start_line=10, end_line=20)

    citations = attach_citations(["b", "a"], [a, b])

    assert [c.chunk_id for c in citations] == ["b", "a"]


def test_attach_citations_copies_every_field_from_hybrid_result_chunk_not_from_llm_text() -> None:
    candidate = _reranked_result(
        "a", file="src/thing.py", symbol="Thing.do", start_line=42, end_line=50
    )

    citations = attach_citations(["a"], [candidate])

    chunk = candidate.hybrid_result.chunk
    assert citations == [
        Citation(
            chunk_id=chunk.id,
            file=chunk.file,
            symbol=chunk.symbol,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
        )
    ]


def test_attach_citations_silently_drops_unknown_ids_and_logs_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    a = _reranked_result("a")

    with caplog.at_level(logging.WARNING):
        citations = attach_citations(["a", "bogus-id"], [a])

    assert [c.chunk_id for c in citations] == ["a"]
    assert any("bogus-id" in record.getMessage() for record in caplog.records)


def test_attach_citations_deduplicates_repeated_ids_preserving_first_seen_order() -> None:
    a = _reranked_result("a")
    b = _reranked_result("b")

    citations = attach_citations(["a", "b", "a"], [a, b])

    assert [c.chunk_id for c in citations] == ["a", "b"]


def test_attach_citations_returns_empty_list_for_empty_cited_chunk_ids() -> None:
    a = _reranked_result("a")

    assert attach_citations([], [a]) == []


# --- format_citations_markdown --------------------------------------------------- #


def test_format_citations_markdown_groups_by_file_and_sorts_by_start_line() -> None:
    citations = [
        Citation(chunk_id="c2", file="b.py", symbol="two", start_line=20, end_line=25),
        Citation(chunk_id="c1", file="a.py", symbol="one", start_line=10, end_line=15),
        Citation(chunk_id="c0", file="a.py", symbol="zero", start_line=1, end_line=5),
    ]

    rendered = format_citations_markdown(citations)

    lines = rendered.splitlines()
    assert lines == [
        "- `a.py:1-5` (zero)",
        "- `a.py:10-15` (one)",
        "- `b.py:20-25` (two)",
    ]


def test_format_citations_markdown_renders_file_range_and_symbol_per_bullet() -> None:
    citation = Citation(chunk_id="c1", file="src/x.py", symbol="X.run", start_line=3, end_line=9)

    rendered = format_citations_markdown([citation])

    assert rendered == "- `src/x.py:3-9` (X.run)"


def test_format_citations_markdown_returns_empty_string_for_no_citations() -> None:
    assert format_citations_markdown([]) == ""
