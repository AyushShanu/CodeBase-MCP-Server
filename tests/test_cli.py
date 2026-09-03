"""Tests for the `codebase-rag` CLI entrypoint (cli.main)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from codebase_rag_mcp import config
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
    # Day 13: no default value is baked into argparse anymore -- `None` is
    # the sentinel `main()` resolves explicitly via `config._resolve_index_dir`.
    assert args.index_dir is None
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Day 13: `main()` now resolves `--repo`/`--index-dir`/`.env` before
    # dispatching -- chdir to an isolated, `.git`-less, `.env`-less tmp_path
    # so this test's outcome doesn't depend on this machine's real cwd/.env
    # (this repo's own checkout has both). A relative `--index-dir` is now
    # rejected outright, so this test uses an absolute one.
    monkeypatch.chdir(tmp_path)
    # `main()`'s serve dispatch calls the real `importlib.reload(config)` --
    # correct in a real process (fresh each run), but reloading the actual
    # `codebase_rag_mcp.config` module here would rebind classes like
    # `ProviderKeys` to new objects mid-test-session, breaking `isinstance`
    # checks in other test files that already imported the pre-reload
    # class by name (e.g. tests/test_config.py). This test only checks
    # argument plumbing, not reload's effect, so neutralize it.
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    calls: list[dict[str, object]] = []

    def fake_run(
        *, repo_source: str | None, index_dir: object, auto_index: bool, env_path: object = None
    ) -> None:
        calls.append(
            {
                "repo_source": repo_source,
                "index_dir": index_dir,
                "auto_index": auto_index,
                "env_path": env_path,
            }
        )

    monkeypatch.setattr(server_module, "run", fake_run)

    absolute_index_dir = str(tmp_path / "idx-dir")
    exit_code = main(
        ["serve", "--repo", "some/path", "--index-dir", absolute_index_dir, "--no-auto-index"]
    )

    assert exit_code == 0
    assert calls == [
        {
            "repo_source": "some/path",
            "index_dir": Path(absolute_index_dir),
            "auto_index": False,
            "env_path": None,
        }
    ]


def test_main_dispatches_serve_with_auto_index_true_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Day 13: isolate cwd (no .git, no .env) and REPO_SOURCE so
    # `_resolve_effective_source` deterministically resolves to `None`
    # regardless of this machine's real checkout/.env state, and the
    # no-explicit-index-dir case deterministically falls back to the
    # static `config.INDEX_DIR` default.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "REPO_SOURCE", "")
    # See the previous test's comment: neutralize the real
    # `importlib.reload(config)` `main()` performs, to avoid rebinding
    # `codebase_rag_mcp.config`'s classes mid-test-session.
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    calls: list[dict[str, object]] = []

    def fake_run(
        *, repo_source: str | None, index_dir: object, auto_index: bool, env_path: object = None
    ) -> None:
        calls.append(
            {
                "repo_source": repo_source,
                "index_dir": index_dir,
                "auto_index": auto_index,
                "env_path": env_path,
            }
        )

    monkeypatch.setattr(server_module, "run", fake_run)

    exit_code = main(["serve"])

    assert exit_code == 0
    assert calls == [
        {
            "repo_source": None,
            "index_dir": Path(INDEX_DIR),
            "auto_index": True,
            "env_path": None,
        }
    ]
