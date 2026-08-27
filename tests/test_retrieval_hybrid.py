"""Tests for hybrid retrieval (retrieval.hybrid.hybrid_search)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing import bm25, vector
from codebase_rag_mcp.parser.models import SymbolKind
from codebase_rag_mcp.retrieval import hybrid as hybrid_module
from codebase_rag_mcp.retrieval.exceptions import NoIndexAvailableError
from codebase_rag_mcp.retrieval.hybrid import hybrid_search


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


class _ControlledEmbeddings:
    """Deterministic embeddings keyed on exact input text.

    Lets a test place a query vector arbitrarily close to (or orthogonal
    from) a target chunk's vector regardless of shared keywords -- needed
    to prove vector-side (not lexical) matching, unlike
    `test_indexing_vector.py`'s length/char-sum-derived `_FakeEmbeddings`,
    which can't reliably simulate "semantically similar despite zero
    shared keywords." Unmapped text falls back to a fixed default vector.
    """

    vectors: ClassVar[dict[str, list[float]]] = {}
    default: ClassVar[list[float]] = [0.0, 0.0, 1.0]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_ControlledEmbeddings.vectors.get(t, _ControlledEmbeddings.default) for t in texts]


@pytest.fixture(autouse=True)
def _reset_controlled_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _ControlledEmbeddings.vectors = {}
    monkeypatch.setattr(vector, "HuggingFaceEmbeddings", _ControlledEmbeddings)


def _build_vector(chunks: list[Chunk], index_dir: Path) -> None:
    vector.build_index(chunks, index_dir=index_dir)


def _build_bm25(chunks: list[Chunk], index_dir: Path) -> None:
    bm25.build_index(chunks, index_dir=index_dir)


# --- exact-symbol vs. semantic ------------------------------------------------- #


def test_hybrid_search_exact_symbol_query_surfaces_chunk_via_bm25(tmp_path: Path) -> None:
    target = _chunk("target", "function generateToken(user) { return sign(user); }")
    other_a = _chunk("other-a", "function parseConfig(path) { return read(path); }")
    other_b = _chunk("other-b", "class HttpClient { close() {} }")
    index_dir = tmp_path / "idx"
    _build_vector([target, other_a, other_b], index_dir)
    _build_bm25([target, other_a, other_b], index_dir)

    results = hybrid_search("generateToken", index_dir=index_dir)

    assert results[0].chunk.id == "target"
    assert results[0].bm25_rank == 1


def test_hybrid_search_semantic_query_surfaces_chunk_via_vector_only(tmp_path: Path) -> None:
    query = "where do we limit concurrent operations"
    target = _chunk("target", "def alpha(): pass")
    unrelated = _chunk("unrelated", "def beta(): pass")
    _ControlledEmbeddings.vectors = {
        query: [1.0, 0.0, 0.0],
        target.content: [1.0, 0.0, 0.0],
        unrelated.content: [0.0, 1.0, 0.0],
    }
    index_dir = tmp_path / "idx"
    _build_vector([target, unrelated], index_dir)
    _build_bm25([target, unrelated], index_dir)

    results = hybrid_search(query, index_dir=index_dir)

    assert results[0].chunk.id == "target"
    assert results[0].vector_rank == 1
    assert results[0].bm25_rank is None


def test_hybrid_search_chunk_found_by_both_outranks_chunk_found_by_one_side(
    tmp_path: Path,
) -> None:
    # A third distractor chunk (irrelevant to both indexes) keeps BM25's
    # classic idf from landing exactly at N=2, n=1 (log(1.5/1.5) == 0), which
    # would zero out "both"'s BM25 signal entirely -- see test_indexing_bm25.py.
    query = "generateToken"
    both = _chunk("both", "function generateToken(user) { return sign(user); }")
    vector_only = _chunk("vector-only", "class HttpClient { close() {} }")
    distractor = _chunk("distractor", "def unrelated_math(x): return x * 2")
    _ControlledEmbeddings.vectors = {
        query: [1.0, 0.0, 0.0],
        both.content: [1.0, 0.0, 0.0],
        vector_only.content: [1.0, 1.0, 0.0],
        distractor.content: [0.0, 0.0, 1.0],
    }
    index_dir = tmp_path / "idx"
    _build_vector([both, vector_only, distractor], index_dir)
    _build_bm25([both, vector_only, distractor], index_dir)

    results = hybrid_search(query, index_dir=index_dir)

    assert [r.chunk.id for r in results[:2]] == ["both", "vector-only"]
    assert results[0].bm25_rank is not None and results[0].vector_rank is not None
    assert results[1].bm25_rank is None and results[1].vector_rank is not None


# --- RRF scoring ---------------------------------------------------------------- #


def test_reciprocal_rank_fusion_top_result_scores_exactly_one_over_k_plus_one(
    tmp_path: Path,
) -> None:
    query = "test query"
    target = _chunk("target", "def alpha(): pass")
    unrelated = _chunk("unrelated", "def beta(): pass")
    _ControlledEmbeddings.vectors = {
        query: [1.0, 0.0, 0.0],
        target.content: [1.0, 0.0, 0.0],
        unrelated.content: [0.0, 1.0, 0.0],
    }
    index_dir = tmp_path / "idx"
    _build_vector([target, unrelated], index_dir)
    # Deliberately no BM25 index built here -- bm25.load_index raises
    # Bm25NotBuiltError, caught internally, so this exercises the
    # single-source (vector-only) path.

    results = hybrid_search(query, index_dir=index_dir, rrf_k=60)

    assert results[0].chunk.id == "target"
    assert results[0].vector_rank == 1
    assert results[0].bm25_rank is None
    assert results[0].score == pytest.approx(1.0 / (60 + 1))


# --- empty / no-match queries ----------------------------------------------------- #


def test_hybrid_search_empty_string_query_does_not_crash(tmp_path: Path) -> None:
    chunks = [_chunk("a"), _chunk("b")]
    index_dir = tmp_path / "idx"
    _build_vector(chunks, index_dir)
    _build_bm25(chunks, index_dir)

    results = hybrid_search("", index_dir=index_dir)

    assert isinstance(results, list)


def test_hybrid_search_no_real_match_returns_explicit_empty_list(tmp_path: Path) -> None:
    query = "zzzznonexistentqqqq"
    target = _chunk("target", "def alpha(): pass")
    _ControlledEmbeddings.vectors = {
        query: [1.0, 0.0, 0.0],
        target.content: [0.0, 1.0, 0.0],
    }
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)
    _build_bm25([target], index_dir)

    results = hybrid_search(query, index_dir=index_dir)

    assert results == []


# --- index availability ------------------------------------------------------------ #


def test_hybrid_search_raises_no_index_available_error_when_nothing_built(
    tmp_path: Path,
) -> None:
    with pytest.raises(NoIndexAvailableError):
        hybrid_search("anything", index_dir=tmp_path / "nothing-here")


def test_hybrid_search_proceeds_when_only_vector_index_is_built(tmp_path: Path) -> None:
    query = "alpha"
    target = _chunk("target", "def alpha(): pass")
    _ControlledEmbeddings.vectors = {query: [1.0, 0.0, 0.0], target.content: [1.0, 0.0, 0.0]}
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)

    results = hybrid_search(query, index_dir=index_dir)

    assert results
    assert results[0].vector_rank is not None
    assert results[0].bm25_rank is None


# --- pre-loaded index caching (Day 08) --------------------------------------------- #


def test_hybrid_search_uses_given_vector_index_instead_of_loading_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "alpha"
    target = _chunk("target", "def alpha(): pass")
    _ControlledEmbeddings.vectors = {query: [1.0, 0.0, 0.0], target.content: [1.0, 0.0, 0.0]}
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)
    _build_bm25([target], index_dir)
    preloaded_vector_index = vector.load_index(index_dir=index_dir)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("vector.load_index must not be called when vector_index is given")

    monkeypatch.setattr(hybrid_module.vector, "load_index", _fail_if_called)

    results = hybrid_search(query, index_dir=index_dir, vector_index=preloaded_vector_index)

    assert results
    assert results[0].chunk.id == "target"


def test_hybrid_search_uses_given_bm25_index_instead_of_loading_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _chunk("target", "function generateToken(user) { return sign(user); }")
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)
    _build_bm25([target], index_dir)
    preloaded_bm25_index = bm25.load_index(index_dir=index_dir)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("bm25.load_index must not be called when bm25_index is given")

    monkeypatch.setattr(hybrid_module.bm25, "load_index", _fail_if_called)

    results = hybrid_search("generateToken", index_dir=index_dir, bm25_index=preloaded_bm25_index)

    assert results
    assert results[0].chunk.id == "target"


def test_hybrid_search_passes_given_embeddings_through_to_vector_index_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "alpha"
    target = _chunk("target", "def alpha(): pass")
    _ControlledEmbeddings.vectors = {query: [1.0, 0.0, 0.0], target.content: [1.0, 0.0, 0.0]}
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)
    preloaded_vector_index = vector.load_index(index_dir=index_dir)
    preloaded_embeddings = _ControlledEmbeddings()

    captured: dict[str, object] = {}
    real_query = preloaded_vector_index.query

    def _spy_query(text: str, **kwargs: object) -> object:
        captured["embeddings"] = kwargs.get("embeddings")
        return real_query(text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(preloaded_vector_index, "query", _spy_query)

    hybrid_search(
        query,
        index_dir=index_dir,
        vector_index=preloaded_vector_index,
        embeddings=preloaded_embeddings,
    )

    assert captured["embeddings"] is preloaded_embeddings


def test_hybrid_search_with_both_preloaded_indexes_never_touches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "alpha"
    target = _chunk("target", "def alpha(): pass")
    _ControlledEmbeddings.vectors = {query: [1.0, 0.0, 0.0], target.content: [1.0, 0.0, 0.0]}
    index_dir = tmp_path / "idx"
    _build_vector([target], index_dir)
    _build_bm25([target], index_dir)
    preloaded_vector_index = vector.load_index(index_dir=index_dir)
    preloaded_bm25_index = bm25.load_index(index_dir=index_dir)

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_index must not be called when both indexes are given")

    monkeypatch.setattr(hybrid_module.vector, "load_index", _fail_if_called)
    monkeypatch.setattr(hybrid_module.bm25, "load_index", _fail_if_called)

    results = hybrid_search(
        query,
        index_dir=tmp_path / "nonexistent-index-dir-never-touched",
        vector_index=preloaded_vector_index,
        bm25_index=preloaded_bm25_index,
    )

    assert results
    assert results[0].chunk.id == "target"
