"""End-to-end tests for cache-aware incremental indexing
(`indexing.repo.build_all_indexes_incremental`).

Mirrors `tests/test_indexing_repo.py`'s real-scan/real-parse/real-chunk
testing style (no fake parser/chunker) but fakes `HuggingFaceEmbeddings`
(`tests/test_indexing_vector.py`'s `_FakeEmbeddings` pattern, duplicated
here per this project's no-shared-conftest convention) so no real model
download happens.

Every test scans a `root = tmp_path / "repo"` directory and persists its
index to a SIBLING `index_dir = tmp_path / "idx"` (never nested under
`root`) -- indexing into a directory nested inside the scanned root would
let a second scan walk into the first run's own persisted index files
(`chunk_cache.json`, `vector.faiss`, ...) as if they were source, since
only a bare `"data"` directory name is excluded by
`ingestion.filters.IGNORED_DIR_NAMES`, not an arbitrary index-dir name.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from codebase_rag_mcp.indexing import repo, vector
from codebase_rag_mcp.indexing.bm25 import load_index as load_bm25_index
from codebase_rag_mcp.indexing.repo import build_all_indexes_incremental
from codebase_rag_mcp.indexing.vector import load_index as load_vector_index

_PY_SOURCE_A = b"""\
def foo():
    return 1


def bar():
    return 2
"""

_PY_SOURCE_B = b"""\
class Baz:
    def qux(self):
        return 3
"""

_PY_SOURCE_A_MODIFIED = b"""\
def foo():
    return 100


def bar():
    return 2
"""


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class _FakeEmbeddings:
    """Duplicated from tests/test_indexing_vector.py's helper of the same
    name -- this project has no shared conftest.py."""

    instances: ClassVar[list[dict[str, object]]] = []
    calls: ClassVar[list[list[str]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        _FakeEmbeddings.instances.append(kwargs)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        _FakeEmbeddings.calls.append(list(texts))
        return [[float(len(t)), float(sum(map(ord, t)) % 97), 1.0] for t in texts]


@pytest.fixture(autouse=True)
def _reset_fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEmbeddings.instances = []
    _FakeEmbeddings.calls = []
    monkeypatch.setattr(vector, "HuggingFaceEmbeddings", _FakeEmbeddings)


def _spy(monkeypatch: pytest.MonkeyPatch, obj: object, name: str) -> list[int]:
    """Wrap `obj.name` with a call-counting spy that still calls through to
    the real implementation, following tests/test_indexing_repo.py's own
    spy-wrapping pattern."""
    call_count: list[int] = []
    real = getattr(obj, name)

    def wrapper(*args: object, **kwargs: object) -> object:
        call_count.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, name, wrapper)
    return call_count


# --- unchanged-repo second run: full cache hit --------------------------------------- #


def test_second_run_on_unchanged_repo_skips_parse_chunk_and_embed_for_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"

    first = build_all_indexes_incremental(root, index_dir=index_dir)
    assert first.files_cache_miss == 2
    assert first.files_cache_hit == 0

    parse_calls = _spy(monkeypatch, repo, "parse_file")
    chunk_calls = _spy(monkeypatch, repo, "chunk_file")
    embed_calls = _spy(monkeypatch, repo.vector, "embed_chunks")

    second = build_all_indexes_incremental(root, index_dir=index_dir)

    assert not parse_calls
    assert not chunk_calls
    assert not embed_calls
    assert second.files_cache_hit == 2
    assert second.files_cache_miss == 0
    assert second.files_total == 2


def test_incremental_reindex_produces_behaviorally_identical_query_results(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"

    build_all_indexes_incremental(root, index_dir=index_dir)
    before = load_bm25_index(index_dir=index_dir).query("foo", top_k=5)

    build_all_indexes_incremental(root, index_dir=index_dir)
    after = load_bm25_index(index_dir=index_dir).query("foo", top_k=5)

    assert [(r.chunk.id, r.score) for r in before] == [(r.chunk.id, r.score) for r in after]


# --- modifying one file --------------------------------------------------------------- #


def test_modifying_one_file_reparses_and_reembeds_only_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"
    build_all_indexes_incremental(root, index_dir=index_dir)

    _write(root / "a.py", _PY_SOURCE_A_MODIFIED)
    parse_calls: list[Path] = []
    real_parse_file = repo.parse_file

    def spy_parse_file(logical_path: Path, language: str, source: bytes) -> object:
        parse_calls.append(logical_path)
        return real_parse_file(logical_path, language, source)

    monkeypatch.setattr(repo, "parse_file", spy_parse_file)

    result = build_all_indexes_incremental(root, index_dir=index_dir)

    assert parse_calls == [Path("a.py")]
    assert result.files_cache_hit == 1
    assert result.files_cache_miss == 1


def test_modifying_one_file_leaves_unrelated_query_results_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"
    build_all_indexes_incremental(root, index_dir=index_dir)
    before = load_bm25_index(index_dir=index_dir).query("Baz", top_k=5)

    _write(root / "a.py", _PY_SOURCE_A_MODIFIED)
    build_all_indexes_incremental(root, index_dir=index_dir)
    after = load_bm25_index(index_dir=index_dir).query("Baz", top_k=5)

    assert [(r.chunk.id, r.score) for r in before] == [(r.chunk.id, r.score) for r in after]


# --- deletion ------------------------------------------------------------------------- #


def test_deleting_a_file_removes_its_chunks_from_vector_bm25_and_reference_indexes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"
    first = build_all_indexes_incremental(root, index_dir=index_dir)
    assert first.files_total == 2

    (root / "b.py").unlink()
    result = build_all_indexes_incremental(root, index_dir=index_dir)

    assert result.files_deleted == 1
    vector_chunks = load_vector_index(index_dir=index_dir).chunks
    bm25_chunks = load_bm25_index(index_dir=index_dir).chunks
    assert "b.py" not in {c.file for c in vector_chunks}
    assert "b.py" not in {c.file for c in bm25_chunks}


# --- BM25/references always fully rebuilt --------------------------------------------- #


def test_bm25_and_reference_index_are_always_fully_rebuilt_not_patched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"
    build_all_indexes_incremental(root, index_dir=index_dir)

    bm25_calls = _spy(monkeypatch, repo.bm25, "build_index")
    references_calls = _spy(monkeypatch, repo.references, "build_index")

    build_all_indexes_incremental(root, index_dir=index_dir)

    assert len(bm25_calls) == 1
    assert len(references_calls) == 1


# --- force -------------------------------------------------------------------------- #


def test_force_bypasses_cache_even_when_hashes_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    _write(root / "b.py", _PY_SOURCE_B)
    index_dir = tmp_path / "idx"
    build_all_indexes_incremental(root, index_dir=index_dir)

    result = build_all_indexes_incremental(root, index_dir=index_dir, force=True)

    assert result.force_used is True
    assert result.files_cache_hit == 0
    assert result.files_cache_miss == 2


def test_force_run_repopulates_cache_for_next_incremental_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    index_dir = tmp_path / "idx"
    build_all_indexes_incremental(root, index_dir=index_dir, force=True)

    result = build_all_indexes_incremental(root, index_dir=index_dir)

    assert result.files_cache_hit == 1
    assert result.files_cache_miss == 0


# --- missing/corrupt cache degrades safely --------------------------------------------- #


def test_missing_chunk_cache_file_falls_back_to_full_index_no_crash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    index_dir = tmp_path / "idx"

    result = build_all_indexes_incremental(root, index_dir=index_dir)

    assert result.files_cache_miss == 1
    assert result.vector_stats.chunks_embedded > 0


def test_corrupt_chunk_cache_file_falls_back_to_full_index_no_crash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py", _PY_SOURCE_A)
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True)
    (index_dir / "chunk_cache.json").write_text("{not valid json", encoding="utf-8")

    result = build_all_indexes_incremental(root, index_dir=index_dir)

    assert result.files_cache_miss == 1
    assert result.vector_stats.chunks_embedded > 0
