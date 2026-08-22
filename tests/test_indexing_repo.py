"""Tests for repo-wide chunk collection (indexing.repo.collect_repo_chunks)."""

from __future__ import annotations

from pathlib import Path

from codebase_rag_mcp.chunker.chunker import chunk_file
from codebase_rag_mcp.indexing.repo import collect_repo_chunks
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
