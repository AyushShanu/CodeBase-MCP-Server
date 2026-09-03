"""Tests for Day 13's cwd-independent INDEX_DIR/.env resolution.

Covers `config._resolve_index_dir` and `config._resolve_env_path` directly
(no subprocess) -- these are the two pure functions that make the same
repo resolve to the same index directory regardless of which directory or
MCP client launched `codebase-rag serve`, and that pick which single
`.env` file (if any) to load. See
`.claude/specs/13-cross-agent-mcp-packaging-portability.md` and
DECISIONS.md D-027.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest
from dotenv import load_dotenv

from codebase_rag_mcp import config
from codebase_rag_mcp.mcp.exceptions import InvalidIndexDirError

_INDEX_ROOT = Path(platformdirs.user_data_dir("codebase-rag")) / "index"


# --- _resolve_index_dir ------------------------------------------------------------- #


def test_resolve_index_dir_two_local_repos_do_not_collide(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    dir_a = config._resolve_index_dir(str(repo_a), None)
    dir_b = config._resolve_index_dir(str(repo_b), None)

    assert dir_a != dir_b
    assert dir_a.parent == _INDEX_ROOT
    assert dir_b.parent == _INDEX_ROOT


def test_resolve_index_dir_two_remote_urls_do_not_collide() -> None:
    dir_a = config._resolve_index_dir("https://github.com/a/repo", None)
    dir_b = config._resolve_index_dir("https://github.com/b/repo", None)

    assert dir_a != dir_b
    assert dir_a.parent == _INDEX_ROOT


def test_resolve_index_dir_same_local_repo_same_result_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    cwd_a = tmp_path / "elsewhere-a"
    cwd_b = tmp_path / "elsewhere-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    monkeypatch.chdir(cwd_a)
    resolved_from_a = config._resolve_index_dir(str(repo), None)

    monkeypatch.chdir(cwd_b)
    resolved_from_b = config._resolve_index_dir(str(repo), None)

    assert resolved_from_a == resolved_from_b


def test_resolve_index_dir_url_normalization_trailing_slash_and_dotgit_equivalent() -> None:
    variants = [
        "https://github.com/a/repo",
        "https://github.com/a/repo.git",
        "https://github.com/a/repo.git/",
        "https://GITHUB.com/a/repo",
    ]
    resolved = {config._resolve_index_dir(v, None) for v in variants}
    assert len(resolved) == 1


def test_resolve_index_dir_explicit_absolute_always_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom-index"
    resolved = config._resolve_index_dir("https://github.com/a/repo", explicit)
    assert resolved == explicit


def test_resolve_index_dir_explicit_relative_raises_invalid_index_dir_error() -> None:
    with pytest.raises(InvalidIndexDirError):
        config._resolve_index_dir("https://github.com/a/repo", "relative/path")


def test_resolve_index_dir_no_repo_source_and_no_explicit_falls_back_to_static_default() -> None:
    assert config._resolve_index_dir(None, None) == Path(config.INDEX_DIR)
    assert config._resolve_index_dir("", None) == Path(config.INDEX_DIR)


# --- _resolve_env_path -------------------------------------------------------------- #


def test_resolve_env_path_explicit_wins_even_if_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.env"
    assert config._resolve_env_path("", explicit=missing) == missing


def test_resolve_env_path_repo_root_env_used_when_local_source_and_no_explicit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    repo_env = repo / ".env"
    repo_env.write_text("FOO=bar\n")

    assert config._resolve_env_path(str(repo), explicit=None) == repo_env


def test_resolve_env_path_skips_repo_root_for_remote_url_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    # A remote URL source has no filesystem ".env" to find beside it --
    # this must fall through to the cwd tier (empty here) rather than
    # erroring or attempting to treat the URL as a local path.
    assert config._resolve_env_path("https://github.com/a/repo", explicit=None) is None


def test_resolve_env_path_cwd_fallback_when_no_explicit_and_no_repo_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cwd_env = tmp_path / ".env"
    cwd_env.write_text("FOO=bar\n")

    assert config._resolve_env_path("", explicit=None) == cwd_env


def test_resolve_env_path_none_when_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    assert config._resolve_env_path("", explicit=None) is None


# --- the core invariant: a real process env var is never overridden ----------------- #


def test_env_var_already_in_os_environ_never_overridden_by_discovered_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "from-process-env")
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("NVIDIA_API_KEY=from-dotenv-file\n")

    load_dotenv(dotenv_path, override=False)

    import os

    assert os.environ["NVIDIA_API_KEY"] == "from-process-env"
