"""Cross-encoder reranking of a hybrid candidate pool.

`rerank` consumes `retrieval.hybrid.hybrid_search`'s output as-is -- it
never re-fetches, re-embeds, or re-tokenizes chunk content, only reorders
`HybridQueryResult`s it is handed by scoring each `(query, chunk.content)`
pair through a `sentence_transformers.CrossEncoder`. Loads a fresh
`CrossEncoder` instance per call, the same no-cross-call-caching convention
`indexing.vector.embed_texts` and `retrieval.hybrid.hybrid_search` already
use (Day 08's MCP server owns model/index lifecycle once it exists).

**Calling contract with `retrieval.hybrid.hybrid_search`:** callers
intending to rerank must call `hybrid_search(query,
top_k=HYBRID_CANDIDATE_POOL_SIZE, ...)` -- i.e. pass the wide pool size as
`top_k`, never rely on `hybrid_search`'s own default `top_k=10`.
`hybrid_search`'s default truncates the merged RRF list *before* reranking
ever sees it, defeating the entire purpose of this stage (CLAUDE.md:
"reranks a *larger* hybrid candidate pool"). See DECISIONS.md and FLOW.md
Section 3.

**Score scale:** `rerank_score` (a raw CrossEncoder logit) and
`HybridQueryResult.score` (RRF) are never combined, averaged, or
renormalized against each other anywhere in this module -- `rerank_score`
alone determines output order.
"""

from __future__ import annotations

import logging

from sentence_transformers import CrossEncoder

from codebase_rag_mcp.config import RERANK_TOP_N, RERANKER_MAX_LENGTH, RERANKER_MODEL_NAME
from codebase_rag_mcp.reranker.exceptions import RerankerModelError
from codebase_rag_mcp.reranker.models import RerankedResult
from codebase_rag_mcp.retrieval.models import HybridQueryResult

logger = logging.getLogger(__name__)


def rerank(
    query: str,
    candidates: list[HybridQueryResult],
    *,
    top_n: int = RERANK_TOP_N,
    model_name: str = RERANKER_MODEL_NAME,
    max_length: int = RERANKER_MAX_LENGTH,
    cross_encoder: CrossEncoder | None = None,
) -> list[RerankedResult]:
    """Score every `(query, candidate.chunk.content)` pair with a fresh
    `CrossEncoder(model_name, max_length=max_length)`, sort by
    `rerank_score` descending, and return the top `min(top_n,
    len(candidates))` as `list[RerankedResult]`.

    Pass an already-constructed `cross_encoder` to reuse it instead --
    `rerank` then skips `CrossEncoder(model_name, max_length=max_length)`
    entirely and calls `.predict()` on the given instance. Omitting it (the
    default, `None`) is byte-for-byte identical to every prior day's
    behavior. Day 08's MCP server is the first real caller of this
    parameter, passing its startup-cached instance so the ~9.7-9.9s
    construction cost D-021 measured never happens per query (see
    DECISIONS.md D-023).

    Returns `[]` immediately for `candidates == []` -- without constructing
    a `CrossEncoder` at all (even if `cross_encoder` was given), mirroring
    `indexing.vector.embed_chunks`'s own "skip the model entirely when
    there's nothing to embed" short-circuit -- so an empty hybrid result
    never pays real model-load cost. Never validates or assumes a minimum
    `len(candidates)` (a small repo may genuinely have fewer real matches
    than `HYBRID_CANDIDATE_POOL_SIZE`); only ever caps output at `top_n`.

    Calls `CrossEncoder.predict()` exactly once with the full batched list
    of pairs -- never once per candidate -- mirroring
    `indexing.vector.embed_texts`'s single batched `embed_documents` call.

    Raises `RerankerModelError` if the model fails to construct or
    `.predict()` raises.
    """
    if not candidates:
        return []

    if cross_encoder is not None:
        model = cross_encoder
    else:
        try:
            model = CrossEncoder(model_name, max_length=max_length)
        except Exception as exc:
            raise RerankerModelError(
                f"failed to load reranker model {model_name!r}: {exc}"
            ) from exc

    pairs = [(query, candidate.chunk.content) for candidate in candidates]
    try:
        raw_scores = model.predict(pairs)
    except Exception as exc:
        raise RerankerModelError(f"failed to score {len(pairs)} pair(s): {exc}") from exc

    scored = sorted(
        zip(candidates, raw_scores, strict=True),
        key=lambda pair: float(pair[1]),
        reverse=True,
    )

    results = [
        RerankedResult(hybrid_result=candidate, rerank_score=float(score), rerank_rank=rank)
        for rank, (candidate, score) in enumerate(scored[:top_n], start=1)
    ]
    logger.info(
        "reranked %d candidate(s) -> top %d (model=%s)",
        len(candidates),
        len(results),
        model_name,
    )
    return results


__all__ = ["rerank"]
