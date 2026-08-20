"""Pydantic models for the ingestion pipeline's inputs and outputs.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, so later stages (parser/chunker) get
free validation when consuming ingestion's output.
"""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RepoSourceType(StrEnum):
    GITHUB = "github"
    LOCAL = "local"


class RepoSource(BaseModel):
    """A resolved, ready-to-scan working copy of a repository."""

    model_config = ConfigDict(frozen=True)

    source_type: RepoSourceType
    original: str
    root: Path
    url: str | None = None
    is_temporary: bool = False

    def cleanup(self) -> None:
        """Remove the working copy from disk if it is a temporary clone.

        No-op for local sources (`is_temporary=False`). Cleanup is
        opt-in and never invoked automatically by `load_repo`, `scan`,
        or `ingest`.
        """
        if self.is_temporary and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


class FileRecord(BaseModel):
    """One file observed during a scan, whether included or excluded."""

    model_config = ConfigDict(frozen=True)

    path: str
    language: str
    size_bytes: int
    included: bool
    exclusion_reason: str | None = None


class RepoStats(BaseModel):
    """Aggregate summary of a completed `scan()` call."""

    model_config = ConfigDict(frozen=True)

    root: Path
    total_files_seen: int
    included_count: int
    excluded_count: int
    excluded_by_reason: dict[str, int] = Field(default_factory=dict)
    included_by_language: dict[str, int] = Field(default_factory=dict)
    files: list[FileRecord] = Field(default_factory=list)


__all__ = ["FileRecord", "RepoSource", "RepoSourceType", "RepoStats"]
