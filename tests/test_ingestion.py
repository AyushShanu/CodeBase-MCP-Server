"""Tests for the ingestion pipeline: loader, filters, languages, scanner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codebase_rag_mcp.ingestion import ingest, loader
from codebase_rag_mcp.ingestion.exceptions import (
    InvalidRepoURLError,
    LocalPathNotADirectoryError,
    LocalPathNotFoundError,
    RepoCloneError,
    RepoCloneTimeoutError,
)
from codebase_rag_mcp.ingestion.filters import DEFAULT_MAX_FILE_SIZE_BYTES, IGNORED_DIR_NAMES
from codebase_rag_mcp.ingestion.loader import load_repo
from codebase_rag_mcp.ingestion.models import RepoSourceType
from codebase_rag_mcp.ingestion.scanner import scan


def _write(path: Path, content: bytes = b"hello\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _forbid_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test immediately if `subprocess.run` is ever called."""

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run should not have been called")

    monkeypatch.setattr(loader.subprocess, "run", _fail)


# --- load_repo: local paths ------------------------------------------------ #


def test_load_repo_local_path_valid_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local directory is validated and returned without invoking git."""
    _forbid_subprocess_run(monkeypatch)
    _write(tmp_path / "a.py")

    result = load_repo(str(tmp_path))

    assert result.source_type == RepoSourceType.LOCAL
    assert result.root == tmp_path.resolve()
    assert result.is_temporary is False
    assert result.url is None


def test_load_repo_local_path_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(LocalPathNotFoundError, match="does not exist"):
        load_repo(str(missing))


def test_load_repo_local_path_that_is_a_file_raises(tmp_path: Path) -> None:
    file_path = tmp_path / "f.txt"
    _write(file_path)
    with pytest.raises(LocalPathNotADirectoryError, match="not a directory"):
        load_repo(str(file_path))


# --- load_repo: URL scheme rejection ---------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "ssh://github.com/example/example.git",
        "git://github.com/example/example.git",
        "ext::sh -c id",
        "file:///etc/passwd",
    ],
)
def test_load_repo_rejects_disallowed_scheme(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_subprocess_run(monkeypatch)
    with pytest.raises(InvalidRepoURLError):
        load_repo(url)


# --- load_repo: cloning (mocked, no real network) --------------------------- #


def test_load_repo_https_clone_invokes_git_with_depth_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "DATA_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["timeout"] = kwargs.get("timeout")
        Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(loader.subprocess, "run", _fake_run)

    result = load_repo("https://github.com/example/example", clone_timeout=5.0)

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:6] == [
        "git",
        "clone",
        "--depth",
        "1",
        "--",
        "https://github.com/example/example",
    ]
    assert Path(argv[6]) == result.root
    assert captured["timeout"] == 5.0
    assert result.source_type == RepoSourceType.GITHUB
    assert result.is_temporary is True
    assert result.root.is_dir()
    assert result.url == "https://github.com/example/example"


def test_load_repo_clone_timeout_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(loader, "DATA_DIR", str(tmp_path))

    def _fake_run(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=0.01)

    monkeypatch.setattr(loader.subprocess, "run", _fake_run)

    with pytest.raises(RepoCloneTimeoutError, match="timeout"):
        load_repo("https://github.com/example/example", clone_timeout=0.01)


def test_load_repo_clone_nonzero_exit_raises_clone_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(loader, "DATA_DIR", str(tmp_path))

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 128, stdout="", stderr="fatal: repository not found"
        )

    monkeypatch.setattr(loader.subprocess, "run", _fake_run)

    with pytest.raises(RepoCloneError, match="fatal"):
        load_repo("https://github.com/example/example")


# --- scanner: ignored directories ------------------------------------------ #


@pytest.mark.parametrize("dir_name", sorted(IGNORED_DIR_NAMES))
def test_scanner_excludes_ignored_dir_names(dir_name: str, tmp_path: Path) -> None:
    _write(tmp_path / dir_name / "inside.py")
    _write(tmp_path / "kept.py")

    stats = scan(tmp_path)

    paths = {f.path for f in stats.files}
    assert not any(p.startswith(f"{dir_name}/") for p in paths)
    assert "kept.py" in paths


# --- scanner: lockfiles and binaries ---------------------------------------- #


def test_scanner_excludes_lockfiles(tmp_path: Path) -> None:
    for name in ("package-lock.json", "poetry.lock", "foo.lock"):
        _write(tmp_path / name)

    stats = scan(tmp_path)

    for name in ("package-lock.json", "poetry.lock", "foo.lock"):
        record = next(f for f in stats.files if f.path == name)
        assert record.included is False
        assert record.exclusion_reason == "lockfile"
    assert stats.excluded_by_reason["lockfile"] == 3


def test_scanner_excludes_binary_extensions(tmp_path: Path) -> None:
    for name in ("img.png", "font.woff", "archive.zip", "doc.pdf"):
        _write(tmp_path / name)

    stats = scan(tmp_path)

    for name in ("img.png", "font.woff", "archive.zip", "doc.pdf"):
        record = next(f for f in stats.files if f.path == name)
        assert record.included is False
        assert record.exclusion_reason == "binary_extension"


def test_scanner_excludes_oversized_file(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    _write(big, b"x" * (DEFAULT_MAX_FILE_SIZE_BYTES + 1))

    stats = scan(tmp_path, max_file_size_bytes=DEFAULT_MAX_FILE_SIZE_BYTES)

    record = next(f for f in stats.files if f.path == "big.py")
    assert record.included is False
    assert record.exclusion_reason == "oversized"
    assert stats.excluded_by_reason["oversized"] == 1


# --- scanner: symlinks ------------------------------------------------------- #


def test_scanner_symlink_escape_is_excluded_and_not_read(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    secret = outside / "secret.txt"
    _write(secret, b"do not read me")

    root = tmp_path_factory.mktemp("repo")
    (root / "escape_link").symlink_to(secret)

    stats = scan(root)

    record = next(f for f in stats.files if f.path == "escape_link")
    assert record.included is False
    assert record.exclusion_reason == "symlink_escape"
    assert stats.excluded_by_reason["symlink_escape"] == 1


def test_scanner_symlink_inside_root_is_included(tmp_path: Path) -> None:
    target = tmp_path / "real" / "target.py"
    _write(target, b"print('hi')\n")
    link = tmp_path / "link.py"
    link.symlink_to(target)

    stats = scan(tmp_path)

    record = next(f for f in stats.files if f.path == "link.py")
    assert record.included is True
    assert record.language == "python"
    assert record.size_bytes == target.stat().st_size


def test_scanner_broken_symlink_is_excluded(tmp_path: Path) -> None:
    (tmp_path / "dangling").symlink_to(tmp_path / "does_not_exist.py")

    stats = scan(tmp_path)

    record = next(f for f in stats.files if f.path == "dangling")
    assert record.included is False
    assert record.exclusion_reason == "broken_symlink"


# --- RepoStats correctness --------------------------------------------------- #


def test_repo_stats_counts_and_language_breakdown_on_fixture_tree(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    _write(tmp_path / "b.py")
    _write(tmp_path / "c.ts")
    _write(tmp_path / "node_modules" / "dep.js")
    _write(tmp_path / "poetry.lock")
    _write(tmp_path / "big.bin", b"x" * (DEFAULT_MAX_FILE_SIZE_BYTES + 1))

    stats = scan(tmp_path)

    assert stats.total_files_seen == 5  # node_modules/dep.js is pruned entirely
    assert stats.included_count == 3
    assert stats.excluded_count == 2
    assert stats.excluded_by_reason == {"lockfile": 1, "binary_extension": 1}
    assert stats.included_by_language == {"python": 2, "typescript": 1}


def test_file_record_path_is_repo_relative_posix(tmp_path: Path) -> None:
    _write(tmp_path / "sub" / "nested.py")

    stats = scan(tmp_path)

    record = next(f for f in stats.files if f.included)
    assert not record.path.startswith("/")
    assert (tmp_path / record.path).exists()


def test_file_record_unknown_language_fallback(tmp_path: Path) -> None:
    _write(tmp_path / "weird.xyz")

    stats = scan(tmp_path)

    record = next(f for f in stats.files if f.path == "weird.xyz")
    assert record.language == "unknown"


# --- ingest() end-to-end ----------------------------------------------------- #


def test_ingest_end_to_end_local_path(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    _write(tmp_path / "poetry.lock")

    via_ingest = ingest(str(tmp_path))
    via_direct = scan(tmp_path)

    assert via_ingest.included_count == via_direct.included_count
    assert via_ingest.excluded_by_reason == via_direct.excluded_by_reason
