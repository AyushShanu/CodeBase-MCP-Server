"""Tests for the per-file incremental-indexing cache (indexing.cache)."""

from __future__ import annotations

from pathlib import Path

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing.cache import ChunkCache, ChunkCacheEntry, hash_bytes
from codebase_rag_mcp.parser.models import SymbolKind


def _chunk(
    chunk_id: str,
    *,
    file: str = "a.py",
    symbol: str = "foo",
    start_line: int = 1,
    end_line: int = 2,
    content: str = "def foo():\n    return 1\n",
) -> Chunk:
    return Chunk(
        id=chunk_id,
        repo="",
        file=file,
        symbol=symbol,
        type=SymbolKind.FUNCTION,
        language="python",
        start_line=start_line,
        end_line=end_line,
        content=content,
    )


def _entry(
    file: str = "a.py", *, content_hash: str = "hash-a", chunks: list[Chunk] | None = None
) -> ChunkCacheEntry:
    resolved_chunks = chunks if chunks is not None else [_chunk("c1", file=file)]
    return ChunkCacheEntry(
        file=file,
        content_hash=content_hash,
        chunks=resolved_chunks,
        embeddings={c.id: [0.1, 0.2, 0.3] for c in resolved_chunks},
        references=[],
    )


# --- hash_bytes --------------------------------------------------------------------- #


def test_hash_bytes_is_deterministic_and_sensitive_to_content_change() -> None:
    assert hash_bytes(b"hello") == hash_bytes(b"hello")
    assert hash_bytes(b"hello") != hash_bytes(b"world")


# --- round trip ----------------------------------------------------------------------- #


def test_chunk_cache_write_then_load_roundtrips_entries_exactly(tmp_path: Path) -> None:
    cache = ChunkCache()
    cache.put(_entry("a.py"))
    cache.put(_entry("b.py", content_hash="hash-b"))

    cache.write(tmp_path)
    loaded = ChunkCache.load(tmp_path)

    assert len(loaded) == 2
    assert loaded.get("a.py", "hash-a") is not None
    assert loaded.get("b.py", "hash-b") is not None


def test_chunk_cache_load_returns_empty_cache_when_file_missing(tmp_path: Path) -> None:
    loaded = ChunkCache.load(tmp_path / "does-not-exist")
    assert len(loaded) == 0


def test_chunk_cache_load_returns_empty_cache_on_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "chunk_cache.json").write_text("{not valid json", encoding="utf-8")
    loaded = ChunkCache.load(tmp_path)
    assert len(loaded) == 0


# --- get / put -------------------------------------------------------------------------- #


def test_chunk_cache_get_returns_none_for_unknown_file() -> None:
    cache = ChunkCache()
    assert cache.get("nope.py", "any-hash") is None


def test_chunk_cache_get_returns_none_on_hash_mismatch() -> None:
    cache = ChunkCache()
    cache.put(_entry("a.py", content_hash="hash-a"))

    assert cache.get("a.py", "hash-a-changed") is None


def test_chunk_cache_get_returns_entry_on_hash_match() -> None:
    cache = ChunkCache()
    entry = _entry("a.py", content_hash="hash-a")
    cache.put(entry)

    result = cache.get("a.py", "hash-a")

    assert result == entry


def test_chunk_cache_put_upserts_existing_file_entry() -> None:
    cache = ChunkCache()
    cache.put(_entry("a.py", content_hash="hash-old"))
    cache.put(_entry("a.py", content_hash="hash-new"))

    assert len(cache) == 1
    assert cache.get("a.py", "hash-new") is not None
    assert cache.get("a.py", "hash-old") is None


# --- retain_only (deletion handling) ----------------------------------------------------- #


def test_chunk_cache_retain_only_drops_entries_for_files_no_longer_present() -> None:
    cache = ChunkCache()
    cache.put(_entry("a.py"))
    cache.put(_entry("b.py", content_hash="hash-b"))
    cache.put(_entry("c.py", content_hash="hash-c"))

    dropped = cache.retain_only({"a.py", "c.py"})

    assert dropped == 1
    assert {e.file for e in cache.entries()} == {"a.py", "c.py"}
