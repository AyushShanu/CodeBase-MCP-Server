"""Smoke tests for the codebase-rag-mcp scaffold."""

from __future__ import annotations

import subprocess
import sys

import codebase_rag_mcp
from codebase_rag_mcp import __version__


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert __version__  # non-empty


def test_package_exposes_version() -> None:
    assert codebase_rag_mcp.__version__ == __version__


def test_cli_version_subcommand() -> None:
    """``codebase-rag --version`` should print the package version and exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "codebase_rag_mcp.cli.main", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert __version__ in result.stdout
