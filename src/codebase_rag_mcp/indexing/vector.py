"""Dense vector index (FAISS) over chunk embeddings.

Hand-rolled FAISS `IndexFlatIP` over L2-normalized vectors, not
`langchain_community.vectorstores.FAISS` -- see DECISIONS.md. Every vector
added to the index and every query vector is normalized in exactly one
place (`_l2_normalize`, reached by every caller via `embed_texts`) so
`IndexFlatIP`'s inner product is always a correct cosine similarity.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import faiss
import numpy as np
from langchain_huggingface import HuggingFaceEmbeddings

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.config import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL_NAME, INDEX_DIR
from codebase_rag_mcp.indexing.exceptions import (
    EmbeddingModelError,
    EmptyIndexError,
    IndexLoadError,
    IndexNotBuiltError,
)
from codebase_rag_mcp.indexing.models import (
    IndexedChunk,
    SkippedChunk,
    VectorIndexStats,
    VectorQueryResult,
)

logger = logging.getLogger(__name__)

_FAISS_INDEX_FILENAME = "vector.faiss"
_METADATA_FILENAME = "vector_metadata.json"


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize each row. A near-zero-norm row is left as zeros (never
    divides by ~0). This is the single enforcement point for
    normalization -- callers never normalize ad hoc."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0, 1.0, norms)
    return np.ascontiguousarray(vectors / safe_norms, dtype="float32")


def embed_texts(
    texts: list[str],
    *,
    model_name: str = EMBEDDING_MODEL_NAME,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    embeddings: HuggingFaceEmbeddings | None = None,
) -> np.ndarray:
    """Embed `texts`, returning an (n, dim) float32 array whose rows are
    all L2-normalized (norm ~= 1.0).

    Loads a fresh `HuggingFaceEmbeddings(model_name=...)` per call by
    default (not cached across calls -- a `functools.cache`d singleton
    would leak a stale instance across tests that monkeypatch this class).
    Pass an already-constructed `embeddings` to reuse it instead --
    `embed_texts` then skips construction entirely. Omitting it (the
    default, `None`) is byte-for-byte identical to every prior day's
    behavior. Day 08's MCP server passes its startup-cached instance into
    `VectorIndex.query`'s own `embeddings` parameter so a query-time
    HuggingFace Hub "is this model current" network round-trip never
    happens per query (see DECISIONS.md D-023).

    Calls `embed_documents` once with the full text list, letting
    `sentence-transformers`' own `encode(..., batch_size=...)` do the
    internal batching -- never one call per text. `normalize_embeddings=True`
    is also requested via `encode_kwargs` for the real model's own
    normalization, but `_l2_normalize` is what's actually enforced and
    asserted.

    Raises `EmbeddingModelError` if the model fails to load or embed.
    """
    if embeddings is not None:
        embedder = embeddings
    else:
        try:
            embedder = HuggingFaceEmbeddings(
                model_name=model_name,
                encode_kwargs={"normalize_embeddings": True, "batch_size": batch_size},
            )
        except Exception as exc:
            raise EmbeddingModelError(
                f"failed to load embedding model {model_name!r}: {exc}"
            ) from exc

    try:
        vectors = embedder.embed_documents(texts)
    except Exception as exc:
        raise EmbeddingModelError(f"failed to embed {len(texts)} text(s): {exc}") from exc

    return _l2_normalize(np.asarray(vectors, dtype="float32"))


def embed_chunks(
    chunks: list[Chunk],
    *,
    model_name: str = EMBEDDING_MODEL_NAME,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> tuple[list[Chunk], np.ndarray, list[SkippedChunk]]:
    """Filter empty-content chunks, embed the rest.

    Returns `(embedded_chunks, vectors, skipped)` where `vectors[i]`
    corresponds to `embedded_chunks[i]`. A chunk with empty/whitespace-only
    `content` is never sent to the model -- it's recorded in `skipped` with
    reason `"empty content"` instead of silently dropped or crashing the
    build.
    """
    keep: list[Chunk] = []
    skipped: list[SkippedChunk] = []
    for chunk in chunks:
        if not chunk.content.strip():
            skipped.append(SkippedChunk(chunk_id=chunk.id, reason="empty content"))
            continue
        keep.append(chunk)

    if not keep:
        return [], np.empty((0, 0), dtype="float32"), skipped

    vectors = embed_texts([c.content for c in keep], model_name=model_name, batch_size=batch_size)
    return keep, vectors, skipped


def build_index_from_embeddings(
    embedded_chunks: list[Chunk],
    vectors: np.ndarray,
    skipped: list[SkippedChunk],
    *,
    chunks_requested: int,
    index_dir: str | Path = INDEX_DIR,
) -> VectorIndexStats:
    """FAISS structure construction + persistence ONLY -- no embedding-model
    call of its own. `vectors[i]` must correspond to `embedded_chunks[i]`.

    `build_index` calls this after its own `embed_chunks` call; the
    incremental indexing path (`indexing.repo.build_all_indexes_incremental`)
    calls this directly with a combined cached+freshly-embedded
    `(chunks, vectors)` pair, skipping `embed_chunks` entirely for
    unchanged files. `chunks_requested` is explicit (not inferred from
    `len(embedded_chunks) + len(skipped)`) because the incremental
    caller's "requested" count spans a cache-hit-union-cache-miss set this
    function has no other way to know.

    Writes `<index_dir>/vector.faiss` (`faiss.write_index`) and
    `<index_dir>/vector_metadata.json` (a JSON array of
    `Chunk.model_dump(mode="json")`, in FAISS-add order -- vector ID `i` is
    metadata row `i`). Creates `index_dir` if needed. Raises
    `EmptyIndexError` if `embedded_chunks` is empty -- identical contract
    to `build_index`.
    """
    if not embedded_chunks:
        raise EmptyIndexError("no chunks produced an embedding vector; nothing to index")

    dimension = vectors.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(vectors)

    out_dir = Path(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out_dir / _FAISS_INDEX_FILENAME))

    metadata = [chunk.model_dump(mode="json") for chunk in embedded_chunks]
    (out_dir / _METADATA_FILENAME).write_text(json.dumps(metadata), encoding="utf-8")

    stats = VectorIndexStats(
        chunks_requested=chunks_requested,
        chunks_embedded=len(embedded_chunks),
        chunks_skipped=len(skipped),
        skipped=skipped,
        embedding_dimension=dimension,
        index_size=index.ntotal,
    )
    logger.info(
        "built vector index at %s: %d embedded, %d skipped, dim=%d",
        out_dir,
        stats.chunks_embedded,
        stats.chunks_skipped,
        stats.embedding_dimension,
    )
    return stats


def build_index(
    chunks: list[Chunk],
    *,
    index_dir: str | Path = INDEX_DIR,
    model_name: str = EMBEDDING_MODEL_NAME,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> VectorIndexStats:
    """Embed `chunks`, build a FAISS `IndexFlatIP`, and persist it plus a
    chunk-metadata sidecar under `index_dir` (default `config.INDEX_DIR`).

    Thin wrapper: `embed_chunks` then `build_index_from_embeddings` --
    zero behavior change from before that extraction. Raises
    `EmptyIndexError` if every chunk is skipped and there is nothing to
    index -- a build must fail loudly, never silently persist an empty
    index.
    """
    embedded_chunks, vectors, skipped = embed_chunks(
        chunks, model_name=model_name, batch_size=batch_size
    )
    return build_index_from_embeddings(
        embedded_chunks,
        vectors,
        skipped,
        chunks_requested=len(chunks),
        index_dir=index_dir,
    )


class VectorIndex:
    """A loaded, queryable FAISS index plus its chunk-metadata store.

    `chunks[i]` is the `Chunk` whose vector holds FAISS ID `i`
    (`IndexFlatIP.add` assigns sequential IDs in insertion order, so a
    plain list gives O(1) ID -> `Chunk` with no extra bookkeeping).
    Construct only via `load_index`; raises `IndexLoadError` if
    `index.ntotal != len(chunks)`, and `EmptyIndexError` if
    `index.ntotal == 0` -- an empty/corrupt index must fail loudly here
    too, not just at build time.
    """

    def __init__(
        self, index: faiss.Index, chunks: list[Chunk], *, model_name: str = EMBEDDING_MODEL_NAME
    ) -> None:
        if index.ntotal != len(chunks):
            raise IndexLoadError(
                f"index/metadata mismatch: {index.ntotal} vectors but {len(chunks)} chunks"
            )
        if index.ntotal == 0:
            raise EmptyIndexError("loaded index has zero vectors")
        self._index = index
        self._chunks = chunks
        self._model_name = model_name

    @property
    def size(self) -> int:
        return self._index.ntotal

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    @property
    def indexed_chunks(self) -> list[IndexedChunk]:
        return [IndexedChunk(chunk=c, vector_id=i) for i, c in enumerate(self._chunks)]

    def query(
        self,
        text: str,
        *,
        top_k: int = 10,
        embeddings: HuggingFaceEmbeddings | None = None,
    ) -> list[VectorQueryResult]:
        """Embed `text` through the exact same `embed_texts` path used at
        build time (so the query vector is L2-normalized identically to
        every indexed vector) and return up to `top_k` nearest chunks by
        cosine similarity (highest score first).

        Pass an already-constructed `embeddings` (see `embed_texts`) to
        reuse it instead of constructing a fresh one for this query --
        purely additive; omitting it preserves today's exact behavior.

        Never raises for an empty or nonsensical `text` -- it still embeds
        to a well-defined (if low-relevance) vector and searches normally.
        """
        query_vector = embed_texts(
            [text], model_name=self._model_name, batch_size=1, embeddings=embeddings
        )
        k = min(top_k, self._index.ntotal)
        scores, ids = self._index.search(query_vector, k)
        results: list[VectorQueryResult] = []
        for score, vector_id in zip(scores[0], ids[0], strict=True):
            if vector_id == -1:
                continue
            results.append(
                VectorQueryResult(chunk=self._chunks[int(vector_id)], score=float(score))
            )
        return results


def load_index(
    *, index_dir: str | Path = INDEX_DIR, model_name: str = EMBEDDING_MODEL_NAME
) -> VectorIndex:
    """Reconstruct a queryable `VectorIndex` from a persisted `index_dir`
    with no rebuild/re-embedding -- a fresh process gets back exactly what
    `build_index` wrote.

    Raises `IndexNotBuiltError` if `index_dir` has no persisted index files
    at all, `IndexLoadError` if the files exist but cannot be parsed or are
    inconsistent with each other.
    """
    in_dir = Path(index_dir)
    faiss_path = in_dir / _FAISS_INDEX_FILENAME
    metadata_path = in_dir / _METADATA_FILENAME

    if not faiss_path.exists() or not metadata_path.exists():
        raise IndexNotBuiltError(f"no persisted index found under {in_dir}; call build_index first")

    try:
        index = faiss.read_index(str(faiss_path))
    except Exception as exc:
        raise IndexLoadError(f"could not read FAISS index at {faiss_path}: {exc}") from exc

    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        chunks = [Chunk.model_validate(item) for item in raw]
    except Exception as exc:
        raise IndexLoadError(f"could not read chunk metadata at {metadata_path}: {exc}") from exc

    return VectorIndex(index, chunks, model_name=model_name)


__all__ = [
    "VectorIndex",
    "build_index",
    "build_index_from_embeddings",
    "embed_chunks",
    "embed_texts",
    "load_index",
]
