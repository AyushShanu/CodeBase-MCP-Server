"""Tests for the per-index manifest (indexing.manifest)."""

from __future__ import annotations

from pathlib import Path

from codebase_rag_mcp.indexing.manifest import (
    MANIFEST_FILENAME,
    IndexManifest,
    load_manifest,
    write_manifest,
)

# --- round trip ------------------------------------------------------------------- #


def test_write_manifest_then_load_manifest_round_trips_repo_root_and_source(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    index_dir = tmp_path / "index"

    write_manifest(index_dir, repo_root=repo_root, source="https://example.com/repo.git")
    loaded = load_manifest(index_dir)

    assert loaded == IndexManifest(
        repo_root=str(repo_root.resolve()), source="https://example.com/repo.git"
    )


def test_write_manifest_resolves_repo_root_to_an_absolute_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    index_dir = tmp_path / "index"

    write_manifest(index_dir, repo_root=repo_root, source="local")
    loaded = load_manifest(index_dir)

    assert loaded is not None
    assert Path(loaded.repo_root).is_absolute()
    assert loaded.repo_root == str(repo_root.resolve())


def test_write_manifest_creates_index_dir_if_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    index_dir = tmp_path / "does" / "not" / "exist" / "yet"

    write_manifest(index_dir, repo_root=repo_root, source="local")

    assert (index_dir / MANIFEST_FILENAME).exists()


# --- missing / malformed ------------------------------------------------------------ #


def test_load_manifest_returns_none_when_manifest_file_is_missing(tmp_path: Path) -> None:
    assert load_manifest(tmp_path / "empty_index_dir") is None


def test_load_manifest_returns_none_for_malformed_json(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / MANIFEST_FILENAME).write_text("{not valid json", encoding="utf-8")

    assert load_manifest(index_dir) is None


def test_load_manifest_returns_none_for_json_missing_required_fields(tmp_path: Path) -> None:
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / MANIFEST_FILENAME).write_text('{"repo_root": "/tmp/x"}', encoding="utf-8")

    assert load_manifest(index_dir) is None
