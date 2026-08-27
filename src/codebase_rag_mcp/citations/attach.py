"""Deterministic mapping from LLM-reported chunk IDs back to real citations.

`generation.pipeline.generate_answer` asks its selected provider for a JSON
object naming which `chunk_id`s it used (`cited_chunk_ids`), but never trusts
anything else the model might claim about a chunk (file path, line numbers,
symbol name). `attach_citations` is the one place that turns an ID into a
full `Citation` -- always by looking up this project's own indexed metadata
in `candidates`, never by parsing or trusting LLM-authored text. An ID the
model names that isn't actually present in `candidates` (a hallucinated or
stale reference) is dropped, not fabricated into a citation.
"""

from __future__ import annotations

import logging

from codebase_rag_mcp.citations.models import Citation
from codebase_rag_mcp.reranker.models import RerankedResult

logger = logging.getLogger(__name__)


def attach_citations(
    cited_chunk_ids: list[str], candidates: list[RerankedResult]
) -> list[Citation]:
    """Map `cited_chunk_ids` to `Citation`s sourced from `candidates`.

    Builds a `chunk.id -> RerankedResult` lookup from `candidates`, then walks
    `cited_chunk_ids` in the order given, emitting one `Citation` per ID with
    every field copied from that ID's `candidate.hybrid_result.chunk`. An ID
    absent from `candidates` is dropped silently (logged at `warning` level,
    never raised -- a model over-citing or citing a stale ID is expected,
    handled input, not a bug). Repeated IDs are de-duplicated, keeping only
    the first occurrence's position. An empty `cited_chunk_ids` list returns
    `[]` without constructing a lookup.
    """
    if not cited_chunk_ids:
        return []

    chunk_lookup = {candidate.hybrid_result.chunk.id: candidate for candidate in candidates}

    citations: list[Citation] = []
    seen_ids: set[str] = set()
    dropped_unknown = 0
    for chunk_id in cited_chunk_ids:
        if chunk_id in seen_ids:
            continue
        candidate = chunk_lookup.get(chunk_id)
        if candidate is None:
            dropped_unknown += 1
            logger.warning(
                "attach_citations: dropping cited_chunk_id %s -- not present in candidates",
                chunk_id,
            )
            continue
        seen_ids.add(chunk_id)
        chunk = candidate.hybrid_result.chunk
        citations.append(
            Citation(
                chunk_id=chunk.id,
                file=chunk.file,
                symbol=chunk.symbol,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
            )
        )

    logger.info(
        "attach_citations: cited=%d resolved=%d dropped_unknown=%d",
        len(cited_chunk_ids),
        len(citations),
        dropped_unknown,
    )
    return citations


__all__ = ["attach_citations"]
