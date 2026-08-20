"""Ignore rules applied during a repository scan.

Every check reports *why* a path is excluded so `RepoStats.excluded_by_reason`
stays meaningful, not just a raw count.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Final

IGNORED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        "node_modules",
        "build",
        "dist",
        "coverage",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".idea",
        ".vscode",
    }
)

LOCKFILE_PATTERNS: Final[tuple[str, ...]] = (
    "*.lock",
    "package-lock.json",
    "poetry.lock",
    "Pipfile.lock",
    "yarn.lock",
    "Cargo.lock",
    "composer.lock",
)

BINARY_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".rar",
        ".7z",
        ".pdf",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".pyc",
        ".pyo",
        ".class",
        ".jar",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".wav",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)

DEFAULT_MAX_FILE_SIZE_BYTES: Final[int] = 1_000_000  # 1 MB


def is_ignored_dir(name: str) -> bool:
    """True if a directory literally named `name` should be pruned whole."""
    return name in IGNORED_DIR_NAMES


def is_lockfile(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in LOCKFILE_PATTERNS)


def is_binary_extension(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def classify_file(
    path: Path,
    size_bytes: int,
    *,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> str | None:
    """Return an exclusion reason, or None if `path` should be included.

    Checked in order: lockfile > binary extension > oversized.
    Directory-name pruning is the scanner's job (via `is_ignored_dir`,
    applied before ever descending into a subtree) -- this function only
    ever sees candidate *files*.
    """
    if is_lockfile(path.name):
        return "lockfile"
    if is_binary_extension(path):
        return "binary_extension"
    if size_bytes > max_file_size_bytes:
        return "oversized"
    return None


__all__ = [
    "BINARY_EXTENSIONS",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "IGNORED_DIR_NAMES",
    "LOCKFILE_PATTERNS",
    "classify_file",
    "is_binary_extension",
    "is_ignored_dir",
    "is_lockfile",
]
