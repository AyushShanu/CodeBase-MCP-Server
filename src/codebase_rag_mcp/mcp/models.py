"""Pydantic models for the MCP server's tool responses.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching every other stage's `models.py`
-- and because `mcp.server.mcpserver.MCPServer`'s `@server.tool()` derives
both the JSON output schema and `structured_content` directly from a
Pydantic return type, with no hand-written JSON Schema needed.

`SearchHit` is a deliberately *flat* projection (file/symbol/line/content/
score), not a nested wrapper around `RerankedResult`/`Chunk` -- the same
"small, external-facing summary, not an internals-inspection object"
reasoning `citations.models.Citation` already established (see
DECISIONS.md D-022/D-023). `ask`'s response is `generation.models
.GeneratedAnswer` directly; it needs no wrapper here since it is already
tool-response-shaped.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SearchHit(BaseModel):
    """One piece of ranked code evidence returned by `search_code` or
    `find_symbol`.

    `score`'s meaning differs by caller: from `search_code`, it is
    `RerankedResult.rerank_score` (a real cross-encoder relevance logit);
    from `find_symbol`, it is a match-quality indicator (`1.0` exact,
    `0.5` qualified-suffix) with no retrieval-relevance meaning at all --
    `find_symbol` is a deterministic lookup, not a ranked search. Every
    field is sourced from real indexed `Chunk` metadata, never approximated.
    """

    model_config = ConfigDict(frozen=True)

    file: str
    symbol: str
    language: str
    start_line: int
    end_line: int
    content: str
    score: float


class SearchCodeResult(BaseModel):
    """`search_code`'s response: the query plus its ranked hits."""

    model_config = ConfigDict(frozen=True)

    query: str
    hits: list[SearchHit]


class FindSymbolResult(BaseModel):
    """`find_symbol`'s response: the queried symbol plus its definition
    location(s). Definitions only -- see `find_symbol`'s own docstring for
    why usages/callers are out of scope until Day 10. An empty `matches`
    list means no definition was found; it is a normal result, not an
    error."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    matches: list[SearchHit]


class FileContextResult(BaseModel):
    """`get_file_context`'s response: the exact, verbatim requested line
    range. `file` echoes the caller's original repo-relative string (not
    the resolved absolute path, which would leak local filesystem layout).
    `end_line` may be lower than what was requested if the file was
    shorter than the requested range -- see `InvalidLineRangeError`'s
    docstring for the clamping rule."""

    model_config = ConfigDict(frozen=True)

    file: str
    start_line: int
    end_line: int
    content: str


__all__ = ["FileContextResult", "FindSymbolResult", "SearchCodeResult", "SearchHit"]
