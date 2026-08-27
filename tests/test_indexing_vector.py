"""Tests for embeddings + the FAISS vector index (indexing.vector)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

import faiss
import numpy as np
import pytest

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing import vector
from codebase_rag_mcp.indexing.exceptions import (
    EmptyIndexError,
    IndexLoadError,
    IndexNotBuiltError,
)
from codebase_rag_mcp.indexing.models import SkippedChunk
from codebase_rag_mcp.indexing.vector import build_index, embed_chunks, embed_texts, load_index
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


class _FakeEmbeddings:
    """Deterministic stand-in for `HuggingFaceEmbeddings`.

    Captures constructor kwargs and every `embed_documents` call's texts
    into module-level-visible closure lists so tests can assert on them,
    matching this repo's `monkeypatch` + closure-capture mocking style (no
    `unittest.mock.Mock`).
    """

    instances: ClassVar[list[dict[str, object]]] = []
    calls: ClassVar[list[list[str]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        _FakeEmbeddings.instances.append(kwargs)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        _FakeEmbeddings.calls.append(list(texts))
        # Deterministic 3-dim vector derived from text length + char sum,
        # deliberately non-unit-length so normalization is actually exercised.
        return [[float(len(t)), float(sum(map(ord, t)) % 97), 1.0] for t in texts]


@pytest.fixture(autouse=True)
def _reset_fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEmbeddings.instances = []
    _FakeEmbeddings.calls = []
    monkeypatch.setattr(vector, "HuggingFaceEmbeddings", _FakeEmbeddings)


# --- embed_texts ------------------------------------------------------------- #


def test_embed_texts_calls_embed_documents_once_with_all_texts() -> None:
    texts = [f"text {i}" for i in range(5)]

    embed_texts(texts)

    assert len(_FakeEmbeddings.calls) == 1
    assert _FakeEmbeddings.calls[0] == texts


def test_embed_texts_passes_batch_size_to_encode_kwargs() -> None:
    embed_texts(["a", "b"], batch_size=7)

    assert _FakeEmbeddings.instances[0]["encode_kwargs"]["batch_size"] == 7  # type: ignore[index]


def test_embed_texts_uses_given_embeddings_instead_of_constructing_one() -> None:
    preloaded = _FakeEmbeddings(model_name="preloaded")
    _FakeEmbeddings.instances = []  # discard the construction just above

    vectors = embed_texts(["a", "b"], embeddings=preloaded)

    assert _FakeEmbeddings.instances == []
    assert len(_FakeEmbeddings.calls) == 1
    assert vectors.shape[0] == 2


def test_embed_texts_vectors_are_l2_normalized() -> None:
    vectors = embed_texts(["short", "a much much longer piece of text than the other"])

    norms = np.linalg.norm(vectors, axis=1)
    for norm in norms:
        assert norm == pytest.approx(1.0, abs=1e-5)


# --- embed_chunks ------------------------------------------------------------- #


def test_embed_chunks_skips_empty_content_chunk_with_reason() -> None:
    chunks = [
        _chunk("a", "def foo(): pass"),
        _chunk("b", "   \n\t  "),
        _chunk("c", "def bar(): pass"),
    ]

    embedded, vectors_out, skipped = embed_chunks(chunks)

    assert [c.id for c in embedded] == ["a", "c"]
    assert skipped == [SkippedChunk(chunk_id="b", reason="empty content")]
    assert _FakeEmbeddings.calls == [["def foo(): pass", "def bar(): pass"]]
    assert vectors_out.shape[0] == 2


def test_embed_chunks_returns_one_vector_per_chunk() -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]

    embedded, vectors_out, skipped = embed_chunks(chunks)

    assert not skipped
    assert len(embedded) == 3
    assert vectors_out.shape[0] == 3


# --- build_index / load_index ------------------------------------------------- #


def test_build_index_raises_empty_index_error_when_all_chunks_skipped(tmp_path: Path) -> None:
    chunks = [_chunk("a", "   ")]

    with pytest.raises(EmptyIndexError):
        build_index(chunks, index_dir=tmp_path / "idx")


def test_build_index_persists_faiss_and_metadata_files(tmp_path: Path) -> None:
    chunks = [_chunk("a"), _chunk("b")]
    index_dir = tmp_path / "idx"

    stats = build_index(chunks, index_dir=index_dir)

    assert (index_dir / "vector.faiss").exists()
    assert (index_dir / "vector_metadata.json").exists()
    metadata = json.loads((index_dir / "vector_metadata.json").read_text())
    assert len(metadata) == 2
    assert stats.chunks_embedded == 2
    assert stats.chunks_requested == 2
    assert stats.chunks_skipped == 0
    assert stats.embedding_dimension == 3
    assert stats.index_size == 2


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


def test_load_index_raises_index_not_built_error_when_missing(tmp_path: Path) -> None:
    with pytest.raises(IndexNotBuiltError):
        load_index(index_dir=tmp_path / "does-not-exist")


def test_load_index_raises_index_load_error_on_corrupt_metadata(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    build_index([_chunk("a"), _chunk("b")], index_dir=index_dir)

    (index_dir / "vector_metadata.json").write_text("not valid json", encoding="utf-8")

    with pytest.raises(IndexLoadError):
        load_index(index_dir=index_dir)


def test_load_index_raises_empty_index_error_for_zero_vector_index(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True)
    empty_index = faiss.IndexFlatIP(3)
    faiss.write_index(empty_index, str(index_dir / "vector.faiss"))
    (index_dir / "vector_metadata.json").write_text("[]", encoding="utf-8")

    with pytest.raises(EmptyIndexError):
        load_index(index_dir=index_dir)


# --- VectorIndex.query --------------------------------------------------------- #


def test_vector_index_query_normalizes_query_vector_like_index_vectors(tmp_path: Path) -> None:
    # Querying with content identical to an indexed chunk's content must
    # score ~1.0 (perfect cosine similarity) *only* if the query vector is
    # normalized exactly the same way as every indexed vector -- any
    # asymmetry between the build-side and query-side normalization would
    # pull this below 1.0 even though the underlying raw vectors are equal.
    chunks = [_chunk("a", "alpha"), _chunk("b", "beta text")]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("alpha", top_k=2)

    top = next(r for r in results if r.chunk.id == "a")
    assert top.score == pytest.approx(1.0, abs=1e-4)


def test_vector_index_query_returns_top_k_ranked_by_score(tmp_path: Path) -> None:
    chunks = [_chunk("a", "alpha"), _chunk("b", "beta text longer"), _chunk("c", "gamma")]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("alpha", top_k=2)

    assert len(results) == 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_vector_index_query_empty_string_does_not_crash(tmp_path: Path) -> None:
    chunks = [_chunk("a"), _chunk("b")]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    assert isinstance(loaded.query(""), list)
    assert isinstance(loaded.query("!!!???"), list)


def test_vector_index_query_uses_given_embeddings_instead_of_constructing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = [_chunk("a", "alpha"), _chunk("b", "beta text")]
    index_dir = tmp_path / "idx"
    build_index(chunks, index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)
    preloaded = _FakeEmbeddings(model_name="preloaded")
    _FakeEmbeddings.instances = []  # discard the construction just above

    def _fail_if_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("HuggingFaceEmbeddings must not be constructed when given")

    monkeypatch.setattr(vector, "HuggingFaceEmbeddings", _fail_if_constructed)

    results = loaded.query("alpha", top_k=2, embeddings=preloaded)

    assert results
    assert _FakeEmbeddings.instances == []


# --- Real-model tests (opt-in, skipped by default) ---------------------------- #

_run_real = pytest.mark.skipif(
    os.getenv("RUN_REAL_EMBEDDING_TESTS") != "1",
    reason="downloads all-MiniLM-L6-v2 from HF Hub on first run; opt-in via RUN_REAL_EMBEDDING_TESTS=1",
)


@_run_real
def test_embed_chunks_real_model_produces_384_dim_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # restore the real HuggingFaceEmbeddings for this test

    chunks = [_chunk("a", "def add(a, b):\n    return a + b\n")]
    _, vectors_out, _ = embed_chunks(chunks)

    assert vectors_out.shape == (1, 384)


@_run_real
def test_vector_index_query_real_model_ranks_near_duplicate_above_unrelated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    near_dup_a = _chunk("dup-a", "def add(first, second):\n    return first + second\n")
    near_dup_b = _chunk("dup-b", "def add(x, y):\n    return x + y\n")
    unrelated = _chunk("unrelated", "class HttpClient:\n    def close(self):\n        pass\n")
    index_dir = tmp_path / "idx"
    build_index([near_dup_a, near_dup_b, unrelated], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query(near_dup_a.content, top_k=3)
    by_id = {r.chunk.id: r.score for r in results}

    assert by_id["dup-b"] > by_id["unrelated"]
    assert by_id["dup-b"] - by_id["unrelated"] > 0.3


@_run_real
def test_vector_index_query_real_model_returns_relevant_chunk_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    target = Chunk(
        id="queue",
        repo="",
        file="src/queue.ts",
        symbol="PQueue.add",
        type=SymbolKind.METHOD,
        language="typescript",
        start_line=42,
        end_line=58,
        content=(
            "class PQueue {\n"
            "  add(fn) {\n"
            "    // enforce the queue's concurrency limit before running fn\n"
            "    if (this.pending >= this.concurrency) {\n"
            "      this.queue.push(fn);\n"
            "      return;\n"
            "    }\n"
            "    this.run(fn);\n"
            "  }\n"
            "}\n"
        ),
    )
    other = _chunk("other", "def unrelated_math(x):\n    return x * 2\n")
    index_dir = tmp_path / "idx"
    build_index([target, other], index_dir=index_dir)
    loaded = load_index(index_dir=index_dir)

    results = loaded.query("queue concurrency limit", top_k=1)

    assert results[0].chunk.file == "src/queue.ts"
    assert results[0].chunk.symbol == "PQueue.add"
    assert results[0].chunk.start_line == 42
    assert results[0].chunk.end_line == 58
