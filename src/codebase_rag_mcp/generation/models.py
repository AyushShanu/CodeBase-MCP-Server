"""Pydantic models for the generation stage's output.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `citations.models`/`reranker.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from codebase_rag_mcp.citations.models import Citation


class GeneratedAnswer(BaseModel):
    """The generation stage's public return type -- `pipeline.generate_answer`'s
    result.

    `citations` is never a stripped/flattened view -- each entry is a full
    `Citation` sourced from real indexed metadata, not from anything the LLM
    asserted (see `citations.attach.attach_citations`). `provider_used` is
    `None` only for the zero-candidates short-circuit, where no provider is
    ever called; for every other path it names whichever provider in the
    fallback chain actually produced the answer. `has_sufficient_evidence`
    reflects the model's own judgment *unless* `citations` came back empty,
    in which case `pipeline.generate_answer` forces it to `False` regardless
    of what the model claimed -- a model cannot assert sufficient evidence
    backed by zero real citations.
    """

    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[Citation]
    has_sufficient_evidence: bool
    provider_used: str | None


class _LLMStructuredOutput(BaseModel):
    """Internal schema a provider's raw response text is validated against.

    Never exposed to callers of `generate_answer` -- `pipeline.py` validates
    a provider's `_extract_json_object`-cleaned text against this schema,
    then uses `cited_chunk_ids` purely as lookup keys into `attach_citations`,
    never trusting `answer`/`has_sufficient_evidence` on their own (see
    `GeneratedAnswer`'s anti-fabrication override).
    """

    model_config = ConfigDict(frozen=True)

    answer: str
    cited_chunk_ids: list[str]
    has_sufficient_evidence: bool


__all__ = ["GeneratedAnswer"]
