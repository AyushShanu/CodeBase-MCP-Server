"""Pydantic models for the impact-analysis stage's output.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching every other stage's `models.py`.

`ImpactResult.definitions` reuses `mcp.models.SearchHit` rather than a
new, near-identical model -- a cross-package import (`impact` depending
on `mcp`) that's slightly unusual directionally (elsewhere `mcp` depends
on lower stages) but is safe and non-circular: `mcp/models.py` itself has
zero imports beyond `pydantic`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from codebase_rag_mcp.mcp.models import SearchHit


class Confidence(StrEnum):
    """Whether a caller/importer is a definitively-resolved match
    (CONFIRMED) or a name-based match that could plausibly refer to a
    different same-named definition (LIKELY). Name-based matching, not
    full type/scope resolution, is an explicitly accepted V2
    simplification -- this labeling exists precisely to make that
    limitation visible rather than overstating precision.
    """

    CONFIRMED = "confirmed"
    LIKELY = "likely"


class CallerInfo(BaseModel):
    """One direct call site of the analyzed symbol.

    `caller_symbol` is the containing symbol's base name with any
    `#partN` oversized-chunk suffix stripped, or `None` when the call
    site falls inside a whole-file-fallback chunk (or no containing
    chunk at all -- e.g. top-level code in a file that also has real
    symbols) -- never a misleading empty string. `file`/`line` are
    always exact, real locations.
    """

    model_config = ConfigDict(frozen=True)

    file: str
    line: int
    caller_symbol: str | None
    confidence: Confidence
    is_likely_test: bool


class ImporterInfo(BaseModel):
    """One file that imports the module the analyzed symbol is defined
    in. Always `Confidence.LIKELY` -- name-based import resolution never
    reaches CONFIRMED (see DECISIONS.md).
    """

    model_config = ConfigDict(frozen=True)

    file: str
    line: int
    confidence: Confidence


class ImpactResult(BaseModel):
    """`analyze_impact`'s response.

    `has_evidence=False` only when there are zero definitions --
    `callers`/`importers` may legitimately both be empty while
    `has_evidence` stays `True`. `*_truncated` is `True` whenever the
    real caller/importer count exceeded
    `impact.analyzer.MAX_IMPACT_REFERENCES_PER_KIND`. `explanation` is
    `None` whenever there is no evidence, or whenever every configured
    LLM provider failed (the deterministic evidence is still returned in
    that case -- see DECISIONS.md).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    definitions: list[SearchHit]
    callers: list[CallerInfo]
    importers: list[ImporterInfo]
    callers_truncated: bool
    importers_truncated: bool
    explanation: str | None
    has_evidence: bool


class _LLMImpactOutput(BaseModel):
    """Internal schema `impact.explain.explain_impact`'s provider
    response is validated against. Never exposed publicly -- mirrors
    `generation.models._LLMStructuredOutput`'s own private-schema
    convention exactly.
    """

    model_config = ConfigDict(frozen=True)

    narrative: str
    referenced_files: list[str]


__all__ = ["CallerInfo", "Confidence", "ImpactResult", "ImporterInfo"]
