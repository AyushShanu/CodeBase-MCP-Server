"""Tests for the sparse BM25 index (indexing.bm25)."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest
from rank_bm25 import BM25Okapi

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing.bm25 import Bm25Index, build_index, load_index, tokenize
from codebase_rag_mcp.indexing.exceptions import (
    Bm25LoadError,
    Bm25NotBuiltError,
    EmptyBm25IndexError,
)
from codebase_rag_mcp.indexing.models import SkippedChunk
from codebase_rag_mcp.parser.models import SymbolKind


def _chunk(chunk_id: str, content: str = "def foo():\n    return 1\n") -> Chunk:
    return Chunk(
        id=chunk_id,
        repo="",
        file=f"{chunk_id}.py",
        symbol="foo",
        type=SymbolKind.FUNCTION,
        language="python",
        start_line=1,
        end_line=2,
        content=content,
    )


# --- tokenize ------------------------------------------------------------- #


def test_tokenize_lowercases_and_splits_on_non_alphanumeric_runs() -> None:
    assert tokenize("Hello, World!") == ["hello", "world"]


def test_tokenize_does_not_split_camelcase() -> None:
    # generateToken is alnum-contiguous -- the literal [^a-zA-Z0-9]+ regex
    # has nothing to split on, so it stays one token (lowercased).
    assert tokenize("generateToken") == ["generatetoken"]


def test_tokenize_splits_snake_case_because_underscore_is_non_alphanumeric() -> None:
    # Unlike camelCase, snake_case *does* split under this scheme -- '_' is
    # itself a non-alphanumeric character the regex matches on.
    assert tokenize("generate_token") == ["generate", "token"]


def test_tokenize_empty_string_returns_empty_list() -> None:
    assert tokenize("") == []
    assert tokenize("   !!! ---") == []


# --- build_index ------------------------------------------------------------ #


def test_build_index_raises_empty_bm25_index_error_when_all_chunks_skipped(
    tmp_path: Path,
) -> None:
    chunks = [_chunk("a", "   ")]

    with pytest.raises(EmptyBm25IndexError):
        build_index(chunks, index_dir=tmp_path / "idx")


def test_build_index_skips_empty_content_chunk_with_reason(tmp_path: Path) -> None:
    chunks = [
        _chunk("a", "def foo(): pass"),
        _chunk("b", "   \n\t  "),
        _chunk("c", "def bar(): pass"),
    ]

    stats = build_index(chunks, index_dir=tmp_path / "idx")

    assert stats.chunks_requested == 3
    assert stats.chunks_indexed == 2
    assert stats.chunks_skipped == 1
    assert stats.skipped == [SkippedChunk(chunk_id="b", reason="empty content")]


def test_build_index_persists_pickle_and_metadata_files(tmp_path: Path) -> None:
    chunks = [_chunk("a", "def foo(): pass"), _chunk("b", "def bar(): pass")]
    index_dir = tmp_path / "idx"

    stats = build_index(chunks, index_dir=index_dir)

    assert (index_dir / "bm25.pkl").exists()
    assert (index_dir / "bm25_metadata.json").exists()
    metadata = json.loads((index_dir / "bm25_metadata.json").read_text())
    assert len(metadata) == 2
    assert stats.chunks_indexed == 2
    assert stats.chunks_requested == 2
    assert stats.chunks_skipped == 0
    assert stats.vocabulary_size > 0
    assert stats.index_size == 2


# --- load_index --------------------------------------------------------------- #


def test_load_index_after_build_index_returns_same_chunk_count(tmp_path: Path) -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)

    loaded = load_index(index_dir=index_dir)

    assert loaded.size == 3


def test_build_index_and_load_index_roundtrip_chunk_fields_exactly(tmp_path: Path) -> None:
    chunks = [
        _chunk("a", "def foo():\n    return 1\n"),
        Chunk(
            id="b",
            repo="my-repo",
            file="pkg/mod.py",
            symbol="Baz.qux",
            type=SymbolKind.METHOD,
            language="python",
            start_line=10,
            end_line=15,
            content="class Baz:\n    def qux(self):\n        return 3\n",
        ),
    ]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)

    loaded = load_index(index_dir=index_dir)

    assert loaded.chunks == chunks


def test_load_index_raises_bm25_not_built_error_when_missing(tmp_path: Path) -> None:
    with pytest.raises(Bm25NotBuiltError):
        load_index(index_dir=tmp_path / "does-not-exist")


def test_load_index_raises_bm25_load_error_on_corrupt_pickle(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    build_index([_chunk("a"), _chunk("b")], index_dir=index_dir)

    (index_dir / "bm25.pkl").write_bytes(b"not a valid pickle")

    with pytest.raises(Bm25LoadError):
        load_index(index_dir=index_dir)


def test_load_index_raises_bm25_load_error_on_corrupt_metadata(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    build_index([_chunk("a"), _chunk("b")], index_dir=index_dir)

    (index_dir / "bm25_metadata.json").write_text("not valid json", encoding="utf-8")

    with pytest.raises(Bm25LoadError):
        load_index(index_dir=index_dir)


def test_load_index_raises_empty_bm25_index_error_for_zero_document_index(
    tmp_path: Path,
) -> None:
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True)
    empty_bm25 = BM25Okapi([["placeholder"]])
    # Force corpus_size to 0 to simulate a degenerate persisted index without
    # relying on BM25Okapi accepting an empty corpus (it does not).
    empty_bm25.corpus_size = 0
    with (index_dir / "bm25.pkl").open("wb") as f:
        pickle.dump({"tokenized_corpus": [], "bm25": empty_bm25}, f)
    (index_dir / "bm25_metadata.json").write_text("[]", encoding="utf-8")

    with pytest.raises(EmptyBm25IndexError):
        load_index(index_dir=index_dir)


# --- Bm25Index.query ------------------------------------------------------------ #


def test_bm25_index_query_exact_symbol_match_ranks_top(tmp_path: Path) -> None:
    target = _chunk("target", "function generateToken(user) { return sign(user); }")
    other_a = _chunk("other-a", "function parseConfig(path) { return read(path); }")
    other_b = _chunk("other-b", "class HttpClient { close() {} }")
    index_dir = tmp_path / "idx"
    build_index([target, other_a, other_b], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("generateToken", top_k=3)

    assert results[0].chunk.id == "target"


def test_bm25_index_query_is_case_insensitive(tmp_path: Path) -> None:
    # A third distractor chunk keeps BM25's classic idf (log((N-n+0.5)/(n+0.5)))
    # from landing exactly at N=2, n=1 -- i.e. log(1.5/1.5) == 0, which would
    # zero out any real signal for a term appearing in just 1 of 2 documents.
    target = _chunk("target", "function generateToken(user) { return sign(user); }")
    other_a = _chunk("other-a", "class HttpClient { close() {} }")
    other_b = _chunk("other-b", "def unrelated_math(x): return x * 2")
    index_dir = tmp_path / "idx"
    build_index([target, other_a, other_b], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("GENERATETOKEN", top_k=3)

    assert results[0].chunk.id == "target"


def test_bm25_index_query_returns_empty_list_for_empty_string(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    build_index([_chunk("a"), _chunk("b")], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    assert loaded.query("") == []


def test_bm25_index_query_returns_empty_list_when_no_term_overlap_at_all(
    tmp_path: Path,
) -> None:
    index_dir = tmp_path / "idx"
    build_index(
        [_chunk("a", "def foo(): pass"), _chunk("b", "def bar(): pass")], index_dir=index_dir
    )
    loaded = load_index(index_dir=index_dir)

    assert loaded.query("zzzznonexistentqqqq") == []


def test_bm25_index_query_tokenization_matches_build_side(tmp_path: Path) -> None:
    # Same third-distractor rationale as the case-insensitivity test above.
    target = _chunk("target", "function GenerateToken(x) { return x; }")
    other_a = _chunk("other-a", "class HttpClient { close() {} }")
    other_b = _chunk("other-b", "def unrelated_math(x): return x * 2")
    index_dir = tmp_path / "idx"
    build_index([target, other_a, other_b], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("generatetoken", top_k=3)

    assert results[0].chunk.id == "target"


def test_bm25_index_rejects_mismatched_corpus_size_on_construction() -> None:
    bm25 = BM25Okapi([["foo"], ["bar"]])

    with pytest.raises(Bm25LoadError):
        Bm25Index(bm25, [_chunk("a")])
