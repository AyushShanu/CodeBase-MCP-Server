"""Tests for repo-wide chunk collection (indexing.repo.collect_repo_chunks)
and single-pass dual-index orchestration (indexing.repo.build_all_indexes).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing import repo
from codebase_rag_mcp.indexing.manifest import load_manifest
from codebase_rag_mcp.indexing.models import Bm25IndexStats, VectorIndexStats
from codebase_rag_mcp.indexing.repo import build_all_indexes, collect_repo_chunks
from codebase_rag_mcp.ingestion.scanner import scan
from codebase_rag_mcp.parser.extractor import parse_file

_PY_SOURCE = b"""\
def foo():
    return 1


def bar():
    return 2
"""

_PY_SOURCE_2 = b"""\
class Baz:
    def qux(self):
        return 3
"""

_MARKDOWN_SOURCE = b"# Hello\n\nThis is a README.\n"


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_collect_repo_chunks_matches_manual_parse_and_chunk_sum(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "b.py", _PY_SOURCE_2)
    stats = scan(tmp_path)

    result = collect_repo_chunks(tmp_path, stats)

    expected_chunks = []
    for record in stats.files:
        if not record.included:
            continue
        source = (tmp_path / record.path).read_bytes()
        parse_result = parse_file(Path(record.path), record.language, source)
        expected_chunks.extend(chunk_file(parse_result, source))

    assert not result.read_failures
    assert {c.id for c in result.chunks} == {c.id for c in expected_chunks}
    assert len(result.chunks) == len(expected_chunks)


def test_collect_repo_chunks_records_unreadable_file_and_continues(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "b.py", _PY_SOURCE_2)
    stats = scan(tmp_path)

    (tmp_path / "b.py").unlink()

    result = collect_repo_chunks(tmp_path, stats)

    assert len(result.read_failures) == 1
    failure = result.read_failures[0]
    assert failure.path == "b.py"
    assert "FileNotFoundError" in failure.reason

    remaining_files = {c.file for c in result.chunks}
    assert remaining_files == {"a.py"}


def test_collect_repo_chunks_records_unsupported_language_file_and_continues(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "README.md", _MARKDOWN_SOURCE)
    stats = scan(tmp_path)

    result = collect_repo_chunks(tmp_path, stats)

    assert len(result.read_failures) == 1
    failure = result.read_failures[0]
    assert failure.path == "README.md"
    assert "UnsupportedLanguageError" in failure.reason

    remaining_files = {c.file for c in result.chunks}
    assert remaining_files == {"a.py"}


def test_collect_repo_chunks_skips_excluded_files(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    # package-lock.json is excluded as a lockfile by ingestion.filters.
    _write(tmp_path / "package-lock.json", b"{}\n")
    stats = scan(tmp_path)

    excluded = [f for f in stats.files if f.path == "package-lock.json"]
    assert excluded and excluded[0].included is False

    result = collect_repo_chunks(tmp_path, stats)

    assert not result.read_failures
    assert {c.file for c in result.chunks} == {"a.py"}


# --- build_all_indexes --------------------------------------------------------- #


class _FakeStats:
    """Stand-in for VectorIndexStats/Bm25IndexStats -- records the chunk
    list it was called with, matching this repo's closure-capture mocking
    style (no unittest.mock.Mock)."""

    vector_calls: ClassVar[list[list[Chunk]]] = []
    bm25_calls: ClassVar[list[list[Chunk]]] = []

    @staticmethod
    def fake_vector_build_index(chunks: list[Chunk], **kwargs: object) -> VectorIndexStats:
        _FakeStats.vector_calls.append(list(chunks))
        return VectorIndexStats(
            chunks_requested=len(chunks),
            chunks_embedded=len(chunks),
            chunks_skipped=0,
            embedding_dimension=3,
            index_size=len(chunks),
        )

    @staticmethod
    def fake_bm25_build_index(chunks: list[Chunk], **kwargs: object) -> Bm25IndexStats:
        _FakeStats.bm25_calls.append(list(chunks))
        return Bm25IndexStats(
            chunks_requested=len(chunks),
            chunks_indexed=len(chunks),
            chunks_skipped=0,
            vocabulary_size=1,
            index_size=len(chunks),
        )


@pytest.fixture(autouse=True)
def _reset_fake_stats() -> None:
    _FakeStats.vector_calls = []
    _FakeStats.bm25_calls = []


def test_build_all_indexes_calls_collect_repo_chunks_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "b.py", _PY_SOURCE_2)

    call_count: list[int] = []
    real_collect_repo_chunks = repo.collect_repo_chunks

    def spy(*args: object, **kwargs: object) -> object:
        call_count.append(1)
        return real_collect_repo_chunks(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "collect_repo_chunks", spy)
    monkeypatch.setattr(repo.vector, "build_index", _FakeStats.fake_vector_build_index)
    monkeypatch.setattr(repo.bm25, "build_index", _FakeStats.fake_bm25_build_index)

    build_all_indexes(tmp_path, index_dir=tmp_path / "idx")

    assert len(call_count) == 1


def test_build_all_indexes_passes_the_same_chunk_list_to_both_builders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "b.py", _PY_SOURCE_2)

    monkeypatch.setattr(repo.vector, "build_index", _FakeStats.fake_vector_build_index)
    monkeypatch.setattr(repo.bm25, "build_index", _FakeStats.fake_bm25_build_index)

    build_all_indexes(tmp_path, index_dir=tmp_path / "idx")

    assert len(_FakeStats.vector_calls) == 1
    assert len(_FakeStats.bm25_calls) == 1
    vector_ids = [c.id for c in _FakeStats.vector_calls[0]]
    bm25_ids = [c.id for c in _FakeStats.bm25_calls[0]]
    assert vector_ids == bm25_ids
    assert vector_ids  # non-empty -- both builders actually received chunks


def test_build_all_indexes_returns_vector_and_bm25_stats_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    _write(tmp_path / "b.py", _PY_SOURCE_2)

    monkeypatch.setattr(repo.vector, "build_index", _FakeStats.fake_vector_build_index)
    monkeypatch.setattr(repo.bm25, "build_index", _FakeStats.fake_bm25_build_index)

    vector_stats, bm25_stats = build_all_indexes(tmp_path, index_dir=tmp_path / "idx")

    assert isinstance(vector_stats, VectorIndexStats)
    assert isinstance(bm25_stats, Bm25IndexStats)
    assert vector_stats.chunks_requested == bm25_stats.chunks_requested


def test_build_all_indexes_writes_a_manifest_with_resolved_repo_root_and_given_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py", _PY_SOURCE)
    monkeypatch.setattr(repo.vector, "build_index", _FakeStats.fake_vector_build_index)
    monkeypatch.setattr(repo.bm25, "build_index", _FakeStats.fake_bm25_build_index)
    index_dir = tmp_path / "idx"

    build_all_indexes(tmp_path, index_dir=index_dir, repo="https://example.com/repo.git")

    manifest = load_manifest(index_dir)
    assert manifest is not None
    assert manifest.repo_root == str(tmp_path.resolve())
    assert manifest.source == "https://example.com/repo.git"


def test_build_all_indexes_manifest_repo_root_matches_the_scanned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _write(checkout / "a.py", _PY_SOURCE)
    monkeypatch.setattr(repo.vector, "build_index", _FakeStats.fake_vector_build_index)
    monkeypatch.setattr(repo.bm25, "build_index", _FakeStats.fake_bm25_build_index)
    index_dir = tmp_path / "idx"

    build_all_indexes(checkout, index_dir=index_dir)

    manifest = load_manifest(index_dir)
    assert manifest is not None
    assert manifest.repo_root == str(checkout.resolve())
