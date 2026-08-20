"""Filesystem walker that turns a checkout root into a RepoStats summary.

Never follows a symlink whose target resolves outside `root`: every
symlink (file or directory) is resolved and containment-checked before
being descended into or recorded as included. An escaping symlink is
excluded with a logged reason, never read.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from codebase_rag_mcp.ingestion.filters import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    classify_file,
    is_ignored_dir,
)
from codebase_rag_mcp.ingestion.languages import UNKNOWN_LANGUAGE, detect_language
from codebase_rag_mcp.ingestion.models import FileRecord, RepoStats

logger = logging.getLogger(__name__)


def scan(root: Path, *, max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES) -> RepoStats:
    """Walk `root`, applying ignore rules and language detection.

    `root` must already exist and be a directory (`loader.load_repo`
    guarantees this). Returns one `FileRecord` per file encountered,
    included or excluded, plus aggregate counts.
    """
    root_real = root.resolve(strict=True)
    files: list[FileRecord] = []
    excluded_by_reason: dict[str, int] = {}
    included_by_language: dict[str, int] = {}
    visited_real_dirs: set[Path] = {root_real}

    def _note_excluded(rel_path: Path, size_bytes: int, reason: str) -> None:
        files.append(
            FileRecord(
                path=rel_path.as_posix(),
                language=UNKNOWN_LANGUAGE,
                size_bytes=size_bytes,
                included=False,
                exclusion_reason=reason,
            )
        )
        excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
        logger.info("excluded %s: %s", rel_path.as_posix(), reason)

    def _process_file(logical_path: Path, physical_path: Path, rel_path: Path) -> None:
        try:
            size_bytes = physical_path.stat().st_size
        except OSError:
            _note_excluded(rel_path, 0, "unreadable")
            return

        reason = classify_file(logical_path, size_bytes, max_file_size_bytes=max_file_size_bytes)
        if reason is not None:
            _note_excluded(rel_path, size_bytes, reason)
            return

        language = detect_language(logical_path)
        files.append(
            FileRecord(
                path=rel_path.as_posix(),
                language=language,
                size_bytes=size_bytes,
                included=True,
                exclusion_reason=None,
            )
        )
        included_by_language[language] = included_by_language.get(language, 0) + 1

    def _walk(logical_dir: Path, physical_dir: Path) -> None:
        try:
            entries = sorted(os.scandir(physical_dir), key=lambda e: e.name)
        except OSError:
            logger.warning("could not list directory %s", physical_dir)
            return

        for entry in entries:
            logical_path = logical_dir / entry.name
            rel_path = logical_path.relative_to(root_real)

            if entry.is_symlink():
                try:
                    resolved = Path(entry.path).resolve(strict=True)
                except OSError:
                    _note_excluded(rel_path, 0, "broken_symlink")
                    continue

                try:
                    resolved.relative_to(root_real)
                except ValueError:
                    _note_excluded(rel_path, 0, "symlink_escape")
                    continue

                if resolved.is_dir():
                    if resolved in visited_real_dirs:
                        _note_excluded(rel_path, 0, "symlink_cycle")
                        continue
                    if is_ignored_dir(entry.name):
                        continue
                    visited_real_dirs.add(resolved)
                    _walk(logical_path, resolved)
                else:
                    _process_file(logical_path, resolved, rel_path)
                continue

            if entry.is_dir(follow_symlinks=False):
                if not is_ignored_dir(entry.name):
                    _walk(logical_path, Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                _process_file(logical_path, Path(entry.path), rel_path)

    _walk(root_real, root_real)

    total_files_seen = len(files)
    included_count = sum(1 for f in files if f.included)
    excluded_count = total_files_seen - included_count

    return RepoStats(
        root=root_real,
        total_files_seen=total_files_seen,
        included_count=included_count,
        excluded_count=excluded_count,
        excluded_by_reason=excluded_by_reason,
        included_by_language=included_by_language,
        files=files,
    )


__all__ = ["scan"]
