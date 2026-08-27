"""Tests for cross-encoder reranking (reranker.rerank.rerank)."""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from typing import ClassVar

import pytest

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import HYBRID_CANDIDATE_POOL_SIZE, RERANKER_MAX_LENGTH
from codebase_rag_mcp.indexing import vector
from codebase_rag_mcp.parser.models import SymbolKind
from codebase_rag_mcp.reranker.exceptions import RerankerModelError
from codebase_rag_mcp.retrieval.hybrid import hybrid_search
from codebase_rag_mcp.retrieval.models import HybridQueryResult

# `reranker/__init__.py` does `from codebase_rag_mcp.reranker.rerank import
# rerank`, which overwrites the `reranker` package's own `rerank` attribute
# (submodule -> function, since both share the name `rerank`) -- unlike
# `retrieval`/`indexing`, whose re-exported names never collide with their
# submodule's own name. `importlib.import_module` bypasses that rebinding
# and always returns the actual `reranker.rerank` module object, which is
# what needs patching below (`rerank.py`'s own `from sentence_transformers
# import CrossEncoder` local binding, mirroring why `test_indexing_vector.py`
# patches `vector.HuggingFaceEmbeddings` rather than the upstream package).
rerank_module = importlib.import_module("codebase_rag_mcp.reranker.rerank")


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


def _hybrid_result(
    chunk_id: str,
    *,
    content: str = "def foo():\n    return 1\n",
    score: float = 0.01,
    bm25_rank: int | None = None,
    bm25_score: float | None = None,
    vector_rank: int | None = None,
    vector_score: float | None = None,
) -> HybridQueryResult:
    return HybridQueryResult(
        chunk=_chunk(chunk_id, content),
        score=score,
        bm25_rank=bm25_rank,
        bm25_score=bm25_score,
        vector_rank=vector_rank,
        vector_score=vector_score,
    )


class _FakeCrossEncoder:
    """Closure-capturing fake for `sentence_transformers.CrossEncoder`,
    matching this repo's `monkeypatch` + ClassVar-capture mocking style (no
    `unittest.mock.Mock`) -- mirrors `_FakeEmbeddings` in
    tests/test_indexing_vector.py.
    """

    instances: ClassVar[list[dict[str, object]]] = []
    calls: ClassVar[list[list[tuple[str, str]]]] = []
    score_map: ClassVar[dict[tuple[str, str], float]] = {}
    default_score: ClassVar[float] = 0.0

    def __init__(self, model_name: str, **kwargs: object) -> None:
        self.model_name = model_name
        _FakeCrossEncoder.instances.append({"model_name": model_name, **kwargs})

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        _FakeCrossEncoder.calls.append(list(pairs))
        return [
            _FakeCrossEncoder.score_map.get(pair, _FakeCrossEncoder.default_score) for pair in pairs
        ]


@pytest.fixture(autouse=True)
def _reset_fake_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCrossEncoder.instances = []
    _FakeCrossEncoder.calls = []
    _FakeCrossEncoder.score_map = {}
    _FakeCrossEncoder.default_score = 0.0
    monkeypatch.setattr(rerank_module, "CrossEncoder", _FakeCrossEncoder)


class _ControlledEmbeddings:
    """Deterministic embeddings keyed on exact input text -- mirrors
    tests/test_retrieval_hybrid.py's helper of the same name, duplicated
    here since this project has no shared conftest.py.
    """

    vectors: ClassVar[dict[str, list[float]]] = {}
    default: ClassVar[list[float]] = [0.0, 1.0]

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


# --- empty input --------------------------------------------------------------- #


def test_rerank_empty_candidates_returns_empty_list_without_constructing_model() -> None:
    results = rerank_module.rerank("query", [])

    assert results == []
    assert _FakeCrossEncoder.instances == []


# --- ordering / scoring ---------------------------------------------------------- #


def test_rerank_returns_results_sorted_by_rerank_score_descending() -> None:
    query = "query"
    a = _hybrid_result("a", content="content-a")
    b = _hybrid_result("b", content="content-b")
    c = _hybrid_result("c", content="content-c")
    _FakeCrossEncoder.score_map = {
        (query, "content-a"): 0.2,
        (query, "content-b"): 0.9,
        (query, "content-c"): 0.5,
    }

    results = rerank_module.rerank(query, [a, b, c])

    assert [r.hybrid_result.chunk.id for r in results] == ["b", "c", "a"]
    assert [r.rerank_score for r in results] == [0.9, 0.5, 0.2]


def test_rerank_rerank_rank_is_one_indexed() -> None:
    query = "query"
    a = _hybrid_result("a", content="content-a")
    b = _hybrid_result("b", content="content-b")
    _FakeCrossEncoder.score_map = {(query, "content-a"): 0.1, (query, "content-b"): 0.9}

    results = rerank_module.rerank(query, [a, b])

    assert results[0].hybrid_result.chunk.id == "b"
    assert results[0].rerank_rank == 1
    assert results[1].hybrid_result.chunk.id == "a"
    assert results[1].rerank_rank == 2


def test_rerank_result_exposes_full_hybrid_result_alongside_rerank_fields() -> None:
    query = "query"
    candidate = _hybrid_result(
        "a",
        content="content-a",
        score=0.03,
        bm25_rank=2,
        bm25_score=1.5,
        vector_rank=1,
        vector_score=0.8,
    )
    _FakeCrossEncoder.score_map = {(query, "content-a"): 0.7}

    results = rerank_module.rerank(query, [candidate])

    assert results[0].hybrid_result == candidate
    assert results[0].hybrid_result.bm25_rank == 2
    assert results[0].hybrid_result.bm25_score == 1.5
    assert results[0].hybrid_result.vector_rank == 1
    assert results[0].hybrid_result.vector_score == 0.8
    assert results[0].rerank_score == 0.7
    assert results[0].rerank_rank == 1


def test_rerank_changes_order_relative_to_hybrid_input_order() -> None:
    query = "query"
    high_rrf_low_relevance = _hybrid_result(
        "high_rrf_low_relevance", content="content-high-rrf", score=0.9
    )
    low_rrf_high_relevance = _hybrid_result(
        "low_rrf_high_relevance", content="content-low-rrf", score=0.1
    )
    _FakeCrossEncoder.score_map = {
        (query, "content-high-rrf"): 0.1,
        (query, "content-low-rrf"): 0.9,
    }

    results = rerank_module.rerank(query, [high_rrf_low_relevance, low_rrf_high_relevance])

    assert results[0].hybrid_result.chunk.id == "low_rrf_high_relevance"


# --- batching / model construction ------------------------------------------------ #


def test_rerank_calls_predict_exactly_once_with_full_batch() -> None:
    query = "query"
    candidates = [_hybrid_result(f"c{i}", content=f"content-{i}") for i in range(5)]

    rerank_module.rerank(query, candidates)

    assert len(_FakeCrossEncoder.calls) == 1
    assert _FakeCrossEncoder.calls[0] == [(query, c.chunk.content) for c in candidates]


def test_rerank_constructs_cross_encoder_with_explicit_max_length() -> None:
    query = "query"
    candidate = _hybrid_result("a", content="content-a")

    rerank_module.rerank(query, [candidate])
    assert _FakeCrossEncoder.instances[-1]["max_length"] == RERANKER_MAX_LENGTH

    rerank_module.rerank(query, [candidate], max_length=256)
    assert _FakeCrossEncoder.instances[-1]["max_length"] == 256


# --- pool size / top_n ------------------------------------------------------------- #


def test_rerank_fewer_candidates_than_pool_size_does_not_raise_or_truncate_early() -> None:
    query = "query"
    candidates = [_hybrid_result(f"c{i}", content=f"content-{i}") for i in range(3)]

    results = rerank_module.rerank(query, candidates, top_n=8)

    assert len(results) == 3


def test_rerank_top_n_caps_output_even_with_more_candidates_available() -> None:
    query = "query"
    candidates = [_hybrid_result(f"c{i}", content=f"content-{i}") for i in range(5)]
    _FakeCrossEncoder.score_map = {(query, f"content-{i}"): float(i) for i in range(5)}

    results = rerank_module.rerank(query, candidates, top_n=2)

    assert len(results) == 2
    assert [r.hybrid_result.chunk.id for r in results] == ["c4", "c3"]


# --- failure paths ------------------------------------------------------------------ #


def test_rerank_wraps_model_construction_failure_in_reranker_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RaisingOnInitCrossEncoder:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            raise OSError("model download failed")

    monkeypatch.setattr(rerank_module, "CrossEncoder", _RaisingOnInitCrossEncoder)

    with pytest.raises(RerankerModelError):
        rerank_module.rerank("query", [_hybrid_result("a")])


def test_rerank_uses_given_cross_encoder_instead_of_constructing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "query"
    candidate = _hybrid_result("a", content="content-a")
    _FakeCrossEncoder.score_map = {(query, "content-a"): 0.7}
    preloaded = _FakeCrossEncoder("preloaded-model")
    _FakeCrossEncoder.instances = []  # discard the construction just above

    def _fail_if_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("CrossEncoder must not be constructed when cross_encoder is given")

    monkeypatch.setattr(rerank_module, "CrossEncoder", _fail_if_constructed)

    results = rerank_module.rerank(query, [candidate], cross_encoder=preloaded)

    assert results[0].rerank_score == 0.7
    assert _FakeCrossEncoder.instances == []
    assert len(_FakeCrossEncoder.calls) == 1


def test_rerank_wraps_predict_failure_in_reranker_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RaisingOnPredictCrossEncoder:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            pass

        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            raise RuntimeError("inference failed")

    monkeypatch.setattr(rerank_module, "CrossEncoder", _RaisingOnPredictCrossEncoder)

    with pytest.raises(RerankerModelError):
        rerank_module.rerank("query", [_hybrid_result("a")])


# --- calling contract with hybrid_search (integration-style) ----------------------- #


def test_hybrid_search_wide_top_k_then_rerank_surfaces_candidate_default_top_k_would_cut_off(
    tmp_path: Path,
) -> None:
    query = "test query"
    fillers = [_chunk(f"filler-{i}", f"filler content {i}") for i in range(1, 11)]
    target = _chunk("target", "target content")
    last_filler = _chunk("filler-12", "filler content 12")
    chunks = [*fillers, target, last_filler]

    embeddings: dict[str, list[float]] = {query: [1.0, 0.0]}
    for i, chunk in enumerate(fillers, start=1):
        theta = 0.01 * i
        embeddings[chunk.content] = [math.cos(theta), math.sin(theta)]
    embeddings[target.content] = [math.cos(0.11), math.sin(0.11)]
    embeddings[last_filler.content] = [math.cos(0.12), math.sin(0.12)]
    _ControlledEmbeddings.vectors = embeddings

    index_dir = tmp_path / "idx"
    _build_vector(chunks, index_dir)
    # Deliberately no BM25 index built here -- ranking is driven purely by
    # vector cosine similarity, making it fully controllable via
    # _ControlledEmbeddings, so the default vs. wide top_k comparison below
    # is unambiguous.

    default_results = hybrid_search(query, index_dir=index_dir)
    assert "target" not in {r.chunk.id for r in default_results}

    wide_results = hybrid_search(query, top_k=HYBRID_CANDIDATE_POOL_SIZE, index_dir=index_dir)
    assert "target" in {r.chunk.id for r in wide_results}

    _FakeCrossEncoder.score_map = {(query, target.content): 100.0}
    _FakeCrossEncoder.default_score = 0.0

    reranked = rerank_module.rerank(query, wide_results)

    assert reranked[0].hybrid_result.chunk.id == "target"
    assert reranked[0].rerank_rank == 1
