"""Obtain a working copy of a repository: clone a GitHub URL or validate
a local path.

Only ``https://`` URLs may be cloned -- every other scheme is rejected
*before* `git` is ever invoked. This is not a shell-injection concern:
`git` is always invoked via `subprocess` with an explicit argument list,
never `shell=True`. The risk closed here is SSRF / protocol abuse
(git's `ssh://` and `ext::` transports can reach internal hosts or leak
credentials via `.netrc` / an active SSH agent) and unbounded resource
use from a hostile or slow remote, which the enforced clone timeout also
closes. See DECISIONS.md D-009.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from codebase_rag_mcp.config import DATA_DIR
from codebase_rag_mcp.ingestion.exceptions import (
    InvalidRepoURLError,
    LocalPathNotADirectoryError,
    LocalPathNotFoundError,
    RepoCloneError,
    RepoCloneTimeoutError,
)
from codebase_rag_mcp.ingestion.models import RepoSource, RepoSourceType

logger = logging.getLogger(__name__)

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"https"})
DEFAULT_CLONE_TIMEOUT_SECONDS: float = 60.0


def _clone_dest_dir() -> Path:
    """Create and return a fresh, unique directory under DATA_DIR/clones."""
    parent = Path(DATA_DIR) / "clones"
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="repo-", dir=parent))


def _clone(url: str, dest: Path, *, timeout: float) -> None:
    """Run `git clone --depth 1 -- <url> <dest>`, translating failures."""
    argv = ["git", "clone", "--depth", "1", "--", url, str(dest)]
    try:
        result = subprocess.run(  # argv list, never shell=True
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoCloneTimeoutError(f"git clone of {url!r} exceeded {timeout}s timeout") from exc

    if result.returncode != 0:
        raise RepoCloneError(
            f"git clone of {url!r} failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def load_repo(source: str, *, clone_timeout: float = DEFAULT_CLONE_TIMEOUT_SECONDS) -> RepoSource:
    """Resolve `source` into a `RepoSource` pointing at a working copy.

    `source` is either an `https://` GitHub (or any git host) URL, which
    is shallow-cloned into a fresh directory under `DATA_DIR/clones/`, or
    a local filesystem path, which is validated in place (no clone).

    Raises `InvalidRepoURLError` for a disallowed URL scheme (checked
    before any subprocess call), `RepoCloneTimeoutError`/`RepoCloneError`
    for clone failures, and `LocalPathNotFoundError`/
    `LocalPathNotADirectoryError` for a bad local path.
    """
    parsed = urlparse(source)
    # A Windows absolute path like "C:\Users\..." also parses as having a
    # scheme ("c") under `urlparse`, since it matches the same `x:...`
    # pattern as a real URL. Every IANA-registered URL scheme is 2+
    # characters (https, ssh, git, ...), while a Windows drive letter is
    # always exactly 1 -- so a single-character "scheme" is never a real
    # URL and must fall through to the local-path branch below, not be
    # rejected as a disallowed one.
    if parsed.scheme and len(parsed.scheme) > 1:
        scheme = parsed.scheme.lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise InvalidRepoURLError(
                f"Refusing to clone {source!r}: scheme {scheme!r} is not "
                "allowed, only 'https' is (SSRF / resource-exhaustion risk)"
            )
        dest = _clone_dest_dir()
        _clone(source, dest, timeout=clone_timeout)
        return RepoSource(
            source_type=RepoSourceType.GITHUB,
            original=source,
            root=dest,
            url=source,
            is_temporary=True,
        )

    path = Path(source)
    if not path.exists():
        raise LocalPathNotFoundError(f"Local path does not exist: {source!r}")
    if not path.is_dir():
        raise LocalPathNotADirectoryError(f"Local path is not a directory: {source!r}")
    return RepoSource(
        source_type=RepoSourceType.LOCAL,
        original=source,
        root=path.resolve(),
        url=None,
        is_temporary=False,
    )


__all__ = ["DEFAULT_CLONE_TIMEOUT_SECONDS", "load_repo"]
