"""Per-index manifest: the one piece of state `get_file_context` needs.

`chunker.models.Chunk.file` is repo-relative by design (see `indexing.repo
.collect_repo_chunks`'s docstring) -- nothing before this module ever
recorded the absolute checkout root anywhere persisted, so a
`codebase-rag serve` process started fresh (a different process, possibly a
different session, than whatever ran `codebase-rag index`) had no way to
resolve a chunk's `file` back to a real path on disk. `write_manifest` is
called by `indexing.repo.build_all_indexes` right after both indexes build
successfully; `load_manifest` is read by `mcp.server`'s `get_file_context`
tool. See DECISIONS.md D-023.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"


class IndexManifest(BaseModel):
    """Kept as a Pydantic model (not a dataclass) per CLAUDE.md's "structured
    outputs via Pydantic" convention, matching `chunker.models`/`retrieval.models`.

    `repo_root` is always an absolute, resolved path -- `write_manifest`
    resolves it before persisting. `source` is whatever the caller passed as
    `indexing.repo.build_all_indexes`'s existing `repo` parameter (the raw
    ingestion source string, e.g. a GitHub URL or local path) -- reused
    as-is rather than introducing a second, possibly-diverging field.
    """

    model_config = ConfigDict(frozen=True)

    repo_root: str
    source: str


def write_manifest(index_dir: str | Path, *, repo_root: Path, source: str) -> None:
    """Persist `<index_dir>/manifest.json` describing this index's repo root.

    `repo_root` is resolved (`strict=True` -- it must exist; this is only
    ever called immediately after a successful scan/build of that same
    root) before being written, so `load_manifest`'s consumer never has to
    re-resolve a possibly-relative path itself. Creates `index_dir` if it
    doesn't exist yet, mirroring `indexing.vector.build_index`'s own
    `mkdir(parents=True, exist_ok=True)` convention.
    """
    resolved_root = repo_root.resolve(strict=True)
    manifest = IndexManifest(repo_root=str(resolved_root), source=source)
    out_dir = Path(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MANIFEST_FILENAME).write_text(manifest.model_dump_json(), encoding="utf-8")


def load_manifest(index_dir: str | Path) -> IndexManifest | None:
    """Load `<index_dir>/manifest.json`, or return `None` if it can't be used.

    Never raises. A missing file is a normal, expected case (an older index
    built before this manifest existed, or one built without ever calling
    `build_all_indexes`) -- `mcp.server`'s `get_file_context` degrades that
    into a clear per-call error rather than failing the whole server.
    Malformed JSON or a schema mismatch (`OSError`, `json.JSONDecodeError`,
    `pydantic.ValidationError`) are logged at `warning` and also return
    `None`, one level more lenient than `indexing.vector.load_index`'s
    "translate to a typed exception" discipline, since absence isn't an
    error condition here the way a corrupt *index* would be.
    """
    manifest_path = Path(index_dir) / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return IndexManifest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("could not read manifest at %s: %s", manifest_path, exc)
        return None


__all__ = ["MANIFEST_FILENAME", "IndexManifest", "load_manifest", "write_manifest"]
