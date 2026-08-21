"""Pydantic models for the chunking stage's output.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `parser.models`/`ingestion.models`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from codebase_rag_mcp.parser.models import SymbolKind


class Chunk(BaseModel):
    """One retrievable, citable slice of source code.

    `id` is deterministic -- derived from `file`, `symbol`, and `start_line`
    -- so re-running chunking on an unchanged file produces identical chunk
    IDs, keeping citations stable across re-indexing runs. `symbol` is `""`
    only for a whole-file fallback chunk (see `chunker.chunk_file`); every
    other chunk carries the (possibly qualified, possibly `#partN`-suffixed)
    symbol name it was extracted from.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    repo: str = ""
    file: str
    symbol: str
    type: SymbolKind
    language: str
    start_line: int
    end_line: int
    content: str


__all__ = ["Chunk"]
