"""Sparse BM25 index (rank-bm25) over chunk text.

Mirrors `indexing/vector.py`'s build/persist/load/query lifecycle shape.
`BM25Okapi` has no native serialization format, so the tokenized corpus plus
BM25 state are persisted via `pickle` (`_BM25_INDEX_FILENAME`) alongside a
JSON chunk-metadata sidecar (`_METADATA_FILENAME`) -- mirroring `vector.py`'s
FAISS-binary-plus-JSON-metadata split. **Only ever unpickle a file this
process itself wrote to `index_dir`; never unpickle index data from an
untrusted or externally-supplied path** -- pickle deserialization of
arbitrary input is a known code-execution risk (see DECISIONS.md).

Tokenization is enforced in exactly one function (`tokenize`), reached
identically at build time and query time -- the same discipline `vector.py`
applies to `_l2_normalize`.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import INDEX_DIR
from codebase_rag_mcp.indexing.exceptions import (
    Bm25LoadError,
    Bm25NotBuiltError,
    EmptyBm25IndexError,
)
from codebase_rag_mcp.indexing.models import Bm25IndexStats, Bm25QueryResult, SkippedChunk

logger = logging.getLogger(__name__)

_BM25_INDEX_FILENAME = "bm25.pkl"
_METADATA_FILENAME = "bm25_metadata.json"
_TOKEN_PATTERN = re.compile(r"[^a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase `text`, split on any run of non-alphanumeric characters.

    Single enforcement point for BM25 tokenization -- every caller (build
    time and query time) goes through this function so the two sides can
    never drift apart. CamelCase/snake_case are *not* split into sub-words
    in this version (`generateToken` stays one token; `generate_token` does
    split, since `_` is itself non-alphanumeric) -- see DECISIONS.md for the
    named limitation.
    """
    return [tok for tok in _TOKEN_PATTERN.split(text.lower()) if tok]


def _tokenize_chunks(
    chunks: list[Chunk],
) -> tuple[list[Chunk], list[list[str]], list[SkippedChunk]]:
    """Filter empty-content chunks, tokenize the rest via `tokenize`.

    Mirrors `vector.embed_chunks`'s empty-content filtering exactly, so the
    BM25 and vector indexes skip the identical chunks for the identical
    reason.
    """
    keep: list[Chunk] = []
    tokenized: list[list[str]] = []
    skipped: list[SkippedChunk] = []
    for chunk in chunks:
        if not chunk.content.strip():
            skipped.append(SkippedChunk(chunk_id=chunk.id, reason="empty content"))
            continue
        keep.append(chunk)
        tokenized.append(tokenize(chunk.content))

    return keep, tokenized, skipped


def build_index(chunks: list[Chunk], *, index_dir: str | Path = INDEX_DIR) -> Bm25IndexStats:
    """Tokenize `chunks`, build a `BM25Okapi` corpus, and persist it plus a
    chunk-metadata sidecar under `index_dir` (default `config.INDEX_DIR`).

    Writes `<index_dir>/bm25.pkl` (`pickle.dump({"tokenized_corpus": ...,
    "bm25": BM25Okapi(...)})`) and `<index_dir>/bm25_metadata.json` (a JSON
    array of `Chunk.model_dump(mode="json")`, in corpus order -- corpus
    index `i` is metadata row `i`, mirroring `vector.py`'s FAISS-vector-ID
    convention). Creates `index_dir` if needed. Raises `EmptyBm25IndexError`
    if every chunk is skipped and there is nothing to index -- a build must
    fail loudly, never silently persist an empty index.
    """
    keep, tokenized_corpus, skipped = _tokenize_chunks(chunks)
    if not keep:
        raise EmptyBm25IndexError("no chunks produced a tokenized document; nothing to index")

    bm25 = BM25Okapi(tokenized_corpus)

    out_dir = Path(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / _BM25_INDEX_FILENAME).open("wb") as f:
        pickle.dump({"tokenized_corpus": tokenized_corpus, "bm25": bm25}, f)

    metadata = [chunk.model_dump(mode="json") for chunk in keep]
    (out_dir / _METADATA_FILENAME).write_text(json.dumps(metadata), encoding="utf-8")

    stats = Bm25IndexStats(
        chunks_requested=len(chunks),
        chunks_indexed=len(keep),
        chunks_skipped=len(skipped),
        skipped=skipped,
        vocabulary_size=len(bm25.idf),
        index_size=bm25.corpus_size,
    )
    logger.info(
        "built BM25 index at %s: %d indexed, %d skipped, vocabulary=%d",
        out_dir,
        stats.chunks_indexed,
        stats.chunks_skipped,
        stats.vocabulary_size,
    )
    return stats


class Bm25Index:
    """A loaded, queryable `BM25Okapi` corpus plus its chunk-metadata store.

    `chunks[i]` is the `Chunk` whose tokenized document holds corpus index
    `i` (the same insertion-order convention `VectorIndex` uses for FAISS
    IDs). Construct only via `load_index`; raises `Bm25LoadError` if
    `bm25.corpus_size != len(chunks)`, and `EmptyBm25IndexError` if
    `bm25.corpus_size == 0` -- an empty/corrupt index must fail loudly here
    too, not just at build time.
    """

    def __init__(self, bm25: BM25Okapi, chunks: list[Chunk]) -> None:
        if bm25.corpus_size != len(chunks):
            raise Bm25LoadError(
                f"index/metadata mismatch: {bm25.corpus_size} documents but {len(chunks)} chunks"
            )
        if bm25.corpus_size == 0:
            raise EmptyBm25IndexError("loaded index has zero documents")
        self._bm25 = bm25
        self._chunks = chunks

    @property
    def size(self) -> int:
        return int(self._bm25.corpus_size)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def query(self, text: str, *, top_k: int = 10) -> list[Bm25QueryResult]:
        """Tokenize `text` via the shared `tokenize`, return up to `top_k`
        highest-scoring chunks (highest score first).

        An empty tokenized query (empty/nonsensical `text`) short-circuits
        to `[]` rather than relying on `BM25Okapi.get_scores` -- an empty
        query token list scores every document identically (all zeros),
        which is not a meaningful ranking. A chunk scoring `<= 0` (no
        positive term overlap at all) is excluded -- BM25 scores are
        non-negative for any real overlap, so `<= 0` means "no lexical
        signal," never a fabricated low-relevance result.
        """
        tokens = tokenize(text)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        order = np.argsort(scores)[::-1]

        results: list[Bm25QueryResult] = []
        for idx in order[:top_k]:
            score = float(scores[idx])
            if score <= 0.0:
                break
            results.append(Bm25QueryResult(chunk=self._chunks[int(idx)], score=score))
        return results


def load_index(*, index_dir: str | Path = INDEX_DIR) -> Bm25Index:
    """Reconstruct a queryable `Bm25Index` from a persisted `index_dir` with
    no rebuild/re-tokenization -- a fresh process gets back exactly what
    `build_index` wrote.

    Raises `Bm25NotBuiltError` if `index_dir` has no persisted index files
    at all, `Bm25LoadError` if the files exist but cannot be parsed or are
    inconsistent with each other. Only ever unpickles
    `<index_dir>/bm25.pkl` -- a file this process itself wrote (see module
    docstring).
    """
    in_dir = Path(index_dir)
    bm25_path = in_dir / _BM25_INDEX_FILENAME
    metadata_path = in_dir / _METADATA_FILENAME

    if not bm25_path.exists() or not metadata_path.exists():
        raise Bm25NotBuiltError(
            f"no persisted BM25 index found under {in_dir}; call build_index first"
        )

    try:
        with bm25_path.open("rb") as f:
            # Only ever reads a file this process itself wrote (see module docstring).
            payload = pickle.load(f)
        bm25 = payload["bm25"]
    except Exception as exc:
        raise Bm25LoadError(f"could not read BM25 index at {bm25_path}: {exc}") from exc

    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        chunks = [Chunk.model_validate(item) for item in raw]
    except Exception as exc:
        raise Bm25LoadError(f"could not read chunk metadata at {metadata_path}: {exc}") from exc

    return Bm25Index(bm25, chunks)


__all__ = ["Bm25Index", "build_index", "load_index", "tokenize"]
