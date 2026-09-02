"""Tests for the `codebase-rag` CLI entrypoint (cli.main)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag_mcp.cli.main import _build_parser, _run_index, main
from codebase_rag_mcp.config import INDEX_DIR
from codebase_rag_mcp.indexing import repo as repo_module
from codebase_rag_mcp.indexing.models import Bm25IndexStats, IncrementalBuildStats, VectorIndexStats
from codebase_rag_mcp.ingestion import loader as loader_module
from codebase_rag_mcp.ingestion.models import RepoSource, RepoSourceType
from codebase_rag_mcp.mcp import server as server_module


def _fake_repo_source(root: Path) -> RepoSource:
    return RepoSource(source_type=RepoSourceType.LOCAL, original=str(root), root=root)


def _fake_stats(*, force_used: bool = False) -> IncrementalBuildStats:
    return IncrementalBuildStats(
        vector_stats=VectorIndexStats(
            chunks_requested=1,
            chunks_embedded=1,
            chunks_skipped=0,
            embedding_dimension=3,
            index_size=1,
        ),
        bm25_stats=Bm25IndexStats(
            chunks_requested=1, chunks_indexed=1, chunks_skipped=0, vocabulary_size=2, index_size=1
        ),
        files_total=1,
        files_cache_hit=0,
        files_cache_miss=1,
        files_deleted=0,
        force_used=force_used,
    )


# --- argument parsing ----------------------------------------------------------------- #


def test_build_parser_accepts_force_flag_on_index_subcommand() -> None:
    parser = _build_parser()

    args_with_force = parser.parse_args(["index", "some/path", "--force"])
    assert args_with_force.force is True

    args_without_force = parser.parse_args(["index", "some/path"])
    assert args_without_force.force is False


def test_build_parser_accepts_repo_index_dir_no_auto_index_flags_on_serve() -> None:
    parser = _build_parser()

    args = parser.parse_args(
        ["serve", "--repo", "some/path", "--index-dir", "idx", "--no-auto-index"]
    )

    assert args.repo == "some/path"
    assert args.index_dir == "idx"
    assert args.no_auto_index is True


def test_build_parser_serve_defaults_when_no_flags_given() -> None:
    parser = _build_parser()

    args = parser.parse_args(["serve"])

    assert args.repo is None
    assert args.index_dir == INDEX_DIR
    assert args.no_auto_index is False


# --- _run_index ------------------------------------------------------------------------ #


def test_run_index_threads_force_flag_into_build_all_indexes_incremental(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_load_repo(source: str, **_kwargs: object) -> RepoSource:
        return _fake_repo_source(tmp_path)

    def fake_build_all_indexes_incremental(
        root: Path, *, index_dir: object, repo: str, force: bool
    ) -> IncrementalBuildStats:
        calls.append({"root": root, "index_dir": index_dir, "repo": repo, "force": force})
        return _fake_stats(force_used=force)

    monkeypatch.setattr(loader_module, "load_repo", fake_load_repo)
    monkeypatch.setattr(
        repo_module, "build_all_indexes_incremental", fake_build_all_indexes_incremental
    )

    exit_code = _run_index("some/source", str(tmp_path / "idx"), force=True)

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["force"] is True


def test_run_index_prints_cache_bypassed_note_when_force_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        loader_module, "load_repo", lambda source, **_k: _fake_repo_source(tmp_path)
    )
    monkeypatch.setattr(
        repo_module,
        "build_all_indexes_incremental",
        lambda root, **_k: _fake_stats(force_used=True),
    )

    _run_index("some/source", str(tmp_path / "idx"), force=True)

    captured = capsys.readouterr()
    assert "--force: cache bypassed" in captured.out


# --- main dispatch ----------------------------------------------------------------------- #


def test_main_dispatches_index_with_force_flag_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[bool] = []

    def fake_load_repo(source: str, **_kwargs: object) -> RepoSource:
        return _fake_repo_source(tmp_path)

    def fake_build_all_indexes_incremental(root: Path, **kwargs: object) -> IncrementalBuildStats:
        calls.append(bool(kwargs.get("force")))
        return _fake_stats(force_used=bool(kwargs.get("force")))

    monkeypatch.setattr(loader_module, "load_repo", fake_load_repo)
    monkeypatch.setattr(
        repo_module, "build_all_indexes_incremental", fake_build_all_indexes_incremental
    )

    exit_code = main(["index", "some/source", "--index-dir", str(tmp_path / "idx"), "--force"])

    assert exit_code == 0
    assert calls == [True]
    captured = capsys.readouterr()
    assert "reparsed/reembedded" in captured.out


def test_main_dispatches_serve_with_repo_index_dir_and_auto_index_flag_threaded_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*, repo_source: str | None, index_dir: object, auto_index: bool) -> None:
        calls.append({"repo_source": repo_source, "index_dir": index_dir, "auto_index": auto_index})

    monkeypatch.setattr(server_module, "run", fake_run)

    exit_code = main(["serve", "--repo", "some/path", "--index-dir", "idx-dir", "--no-auto-index"])

    assert exit_code == 0
    assert calls == [{"repo_source": "some/path", "index_dir": "idx-dir", "auto_index": False}]


def test_main_dispatches_serve_with_auto_index_true_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*, repo_source: str | None, index_dir: object, auto_index: bool) -> None:
        calls.append({"repo_source": repo_source, "index_dir": index_dir, "auto_index": auto_index})

    monkeypatch.setattr(server_module, "run", fake_run)

    exit_code = main(["serve"])

    assert exit_code == 0
    assert calls == [{"repo_source": None, "index_dir": INDEX_DIR, "auto_index": True}]
