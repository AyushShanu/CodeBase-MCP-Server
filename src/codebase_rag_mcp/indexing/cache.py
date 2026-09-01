"""Per-file cache of the expensive parse/chunk/embed outputs, keyed by
content hash -- backs `indexing.repo.build_all_indexes_incremental`'s
"skip unchanged files" behavior.

Tree-sitter parsing/chunking and embedding-model inference are pure
functions of a file's content, so they're the only things this cache
stores; BM25 and the reference index are cheap, deterministic, CPU-only
aggregations with no external model call, and are always fully rebuilt
from the complete current chunk/reference set every run (BM25's IDF
weighting is a whole-corpus quantity a partial update would silently
corrupt) -- see DECISIONS.md.

`embeddings` is keyed by `chunk.id` (not positional) so a `chunks` list
and its embeddings can never silently desync if either is reordered or
filtered independently -- a chunk id's absence from the dict is the one,
unambiguous "this chunk was skipped" signal (mirrors `vector.embed_chunks`'s
own empty-content skip), no sentinel value needed. No `EmbeddingVector`
Pydantic model exists elsewhere in this codebase; plain JSON-native
`list[float]` is the minimal representation, mirroring `vector.py`'s own
plain-JSON metadata sidecar convention (no pickle needed, same as
`indexing.references`'s own reasoning).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import INDEX_DIR
from codebase_rag_mcp.indexing.models import FileReference

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "chunk_cache.json"


def hash_bytes(data: bytes) -> str:
    """sha256 hex digest -- single enforcement point for content hashing,
    reached identically by every cache read/write."""
    return hashlib.sha256(data).hexdigest()


class ChunkCacheEntry(BaseModel):
    """One file's cached parse/chunk/embed output.

    `chunks` is this file's FULL contribution to the corpus -- BM25 and
    the reference index need every chunk regardless of whether it also
    got an embedding vector. `embeddings` maps `chunk.id` -> its vector;
    a `chunk.id` absent from `embeddings` was skipped by `embed_chunks`
    for this file (e.g. empty/whitespace-only content).
    """

    model_config = ConfigDict(frozen=True)

    file: str
    content_hash: str
    chunks: list[Chunk]
    embeddings: dict[str, list[float]]
    references: list[FileReference]


class ChunkCache:
    """An in-memory, file-keyed collection of `ChunkCacheEntry`.

    Construct via `ChunkCache()` (empty) or `ChunkCache.load(index_dir)`
    (never raises -- a missing or corrupt `chunk_cache.json` degrades to
    an empty cache, the same leniency convention `indexing.manifest
    .load_manifest` already established: an absent/broken cache is a
    normal "start fresh" state, not an error condition).
    """

    def __init__(self, entries: dict[str, ChunkCacheEntry] | None = None) -> None:
        self._entries: dict[str, ChunkCacheEntry] = dict(entries) if entries else {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, file: str, content_hash: str) -> ChunkCacheEntry | None:
        """Return the cached entry for `file` if present AND its
        `content_hash` matches -- a hash mismatch (the file changed) is a
        miss, returned as `None`, exactly like an unknown file."""
        entry = self._entries.get(file)
        if entry is None or entry.content_hash != content_hash:
            return None
        return entry

    def put(self, entry: ChunkCacheEntry) -> None:
        """Upsert `entry`, keyed by `entry.file`."""
        self._entries[entry.file] = entry

    def retain_only(self, files: set[str]) -> int:
        """Drop every entry whose `file` is not in `files`; return the
        count dropped.

        Called before `.write()` on every run -- this is what makes a
        deletion/rename (or a file newly excluded by a filter rule on an
        already-cached repo) actually take effect instead of leaving a
        ghost entry that would otherwise persist across every future
        incremental run.
        """
        to_drop = [file for file in self._entries if file not in files]
        for file in to_drop:
            del self._entries[file]
        return len(to_drop)

    def entries(self) -> list[ChunkCacheEntry]:
        return list(self._entries.values())

    def write(self, index_dir: str | Path = INDEX_DIR) -> None:
        """Persist as `<index_dir>/chunk_cache.json` -- a plain JSON array
        of `ChunkCacheEntry.model_dump(mode="json")`, mirroring
        `indexing.references.write_index`'s no-pickle-needed precedent.
        """
        out_dir = Path(index_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump(mode="json") for entry in self._entries.values()]
        (out_dir / _CACHE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, index_dir: str | Path = INDEX_DIR) -> ChunkCache:
        """Reconstruct from `<index_dir>/chunk_cache.json`. Never raises --
        a missing file, malformed JSON, or a schema mismatch all degrade
        to an empty cache (logged at `warning` for the corruption case),
        never a crash. Every file becomes a cache miss on the next
        incremental run, which is a normal, safe fallback -- not
        different in kind from a fresh clone with no cache at all.
        """
        path = Path(index_dir) / _CACHE_FILENAME
        if not path.exists():
            return cls()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = {item["file"]: ChunkCacheEntry.model_validate(item) for item in raw}
        except (OSError, json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            logger.warning("could not read chunk cache at %s: %s", path, exc)
            return cls()

        return cls(entries)


__all__ = ["ChunkCache", "ChunkCacheEntry", "hash_bytes"]
