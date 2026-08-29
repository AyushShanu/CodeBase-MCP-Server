"""Repo-wide reference/import index: a plain grouped lookup, no model.

Mirrors `indexing/bm25.py`'s build/persist/load lifecycle shape, but
persists as plain JSON (`references.json`) -- no pickle needed, unlike
`BM25Okapi`'s opaque object graph. Unlike `vector.build_index`/
`bm25.build_index`, `build_index` here is a PURE, in-memory constructor
with no `index_dir`/I/O -- persistence is a separate `write_index` call.

Absence of a persisted index is a normal, lenient case (mirrors
`indexing.manifest.load_manifest`'s `None`-on-absence convention);
corruption of a persisted-but-broken file is a hard failure
(`ReferenceIndexLoadError`), matching `vector`/`bm25`'s strictness for
actual data corruption. This hybrid deliberately reconciles two of Day
10's own spec instructions -- see DECISIONS.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from codebase_rag_mcp.config import INDEX_DIR
from codebase_rag_mcp.indexing.exceptions import ReferenceIndexLoadError
from codebase_rag_mcp.indexing.models import FileReference
from codebase_rag_mcp.parser.models import ReferenceKind

logger = logging.getLogger(__name__)

_REFERENCES_FILENAME = "references.json"


class ReferenceIndex:
    """A grouped, queryable reference/import table. Construct only via
    `build_index`/`load_index`.

    Groups by `FileReference.name` (`by_name` -- the same bare-trailing-
    name key `impact.symbols.match_symbol_chunks` matches definitions on),
    and separately keeps every IMPORT-kind entry as a flat, `(file,
    line)`-sorted list (`imports`) -- import resolution
    (`impact.analyzer`) is by module path, never by name.
    """

    def __init__(self, references: list[FileReference]) -> None:
        self._references = list(references)
        by_name: dict[str, list[FileReference]] = {}
        imports: list[FileReference] = []
        for ref in self._references:
            by_name.setdefault(ref.name, []).append(ref)
            if ref.kind is ReferenceKind.IMPORT:
                imports.append(ref)
        for bucket in by_name.values():
            bucket.sort(key=lambda r: (r.file, r.line))
        imports.sort(key=lambda r: (r.file, r.line))
        self._by_name = by_name
        self._imports = imports

    @property
    def size(self) -> int:
        return len(self._references)

    @property
    def references(self) -> list[FileReference]:
        return list(self._references)

    def by_name(self, name: str) -> list[FileReference]:
        """All references (CALL or IMPORT) whose `name` == `name`, sorted
        `(file, line)`. Returns `[]` for an unknown name -- never raises.
        """
        return list(self._by_name.get(name, []))

    @property
    def imports(self) -> list[FileReference]:
        return list(self._imports)


def build_index(references: list[FileReference]) -> ReferenceIndex:
    """Group `references` into a queryable `ReferenceIndex`. Pure,
    in-memory, no I/O. Never raises, even for `references == []` -- a
    repo with zero recognized call/import sites is a normal state, unlike
    `EmptyBm25IndexError`'s "an index must have documents" rule, which
    does not apply to this plain lookup table.
    """
    return ReferenceIndex(references)


def write_index(index: ReferenceIndex, *, index_dir: str | Path = INDEX_DIR) -> None:
    """Persist `index` as `<index_dir>/references.json` -- a plain JSON
    array of `FileReference.model_dump(mode="json")`. Creates `index_dir`
    if needed, mirroring every other stage's own `mkdir(parents=True,
    exist_ok=True)`.
    """
    out_dir = Path(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = [ref.model_dump(mode="json") for ref in index.references]
    (out_dir / _REFERENCES_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def load_index(*, index_dir: str | Path = INDEX_DIR) -> ReferenceIndex | None:
    """Reconstruct a `ReferenceIndex` from `<index_dir>/references.json`.

    Returns `None` if the file does not exist -- a normal, lenient case
    (an older index built before Day 10, or a repo with zero recognized
    references). Raises `ReferenceIndexLoadError` if the file exists but
    cannot be parsed/validated -- a genuine data-integrity problem, never
    silently swallowed into "pretend it doesn't exist."
    """
    path = Path(index_dir) / _REFERENCES_FILENAME
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        refs = [FileReference.model_validate(item) for item in raw]
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ReferenceIndexLoadError(f"could not read reference index at {path}: {exc}") from exc
    return ReferenceIndex(refs)


__all__ = ["ReferenceIndex", "build_index", "load_index", "write_index"]
