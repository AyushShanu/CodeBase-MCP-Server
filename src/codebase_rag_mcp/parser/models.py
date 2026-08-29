"""Pydantic models for the parsing stage's inputs and outputs.

Kept as Pydantic models (not dataclasses) per CLAUDE.md's "structured
outputs via Pydantic" convention, matching `ingestion.models`, so the
chunker and later stages get free validation when consuming this stage's
output.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SymbolKind(StrEnum):
    """What kind of structural unit a `ParsedSymbol`/`Chunk` represents.

    `MODULE` is a first-class member, not a separate "fallback kind" bolted
    on elsewhere -- it's what `chunker.chunk_file` assigns to a whole-file
    fallback chunk, so there is exactly one enum for "what kind of thing is
    this" everywhere in the pipeline.
    """

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    MODULE = "module"
    UNKNOWN = "unknown"


class ParsedSymbol(BaseModel):
    """One extracted structural unit (function/class/method/interface).

    `start_line`/`end_line` are 1-indexed, matching editor and citation
    conventions -- Tree-sitter's own node positions are 0-indexed, so the
    conversion happens in `extractor.py` before a `ParsedSymbol` is ever
    constructed.

    For a symbol nested inside a class, `name` is the *qualified* name
    (`f"{class_name}.{method_name}"`, e.g. `"Button.render"`), never a bare
    method name -- two different classes with a same-named method must not
    produce colliding symbol identifiers.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int


class ReferenceKind(StrEnum):
    """CALL = a call-expression naming a symbol; IMPORT = an import/
    import-from statement naming a module. Mirrors SymbolKind's "one enum
    for what kind of thing this is" discipline.
    """

    CALL = "call"
    IMPORT = "import"


class RawReference(BaseModel):
    """One extracted call site or import statement, before it is tagged
    with its originating file (see indexing.models.FileReference for the
    file-tagged version collected repo-wide).

    `name`: for CALL, the callee's bare trailing name (matching
    find_symbol/Chunk.symbol's bare/suffix convention -- `obj.method()`
    records name="method", never "obj.method"). For IMPORT, the module
    specifier's own last dotted/path segment -- used only as the
    reference-index's grouping key; import RESOLUTION is always by
    `module`, never by `name` (see impact.analyzer).
    `kind`: CALL or IMPORT.
    `line`: 1-indexed (matches ParsedSymbol.start_line's convention).
    `module`: None for CALL. For IMPORT, the raw module specifier exactly
    as written in source (e.g. "./mod", "foo.bar") -- never
    resolved/normalized here; resolution happens in impact.analyzer.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: ReferenceKind
    line: int
    module: str | None = None


class ParseResult(BaseModel):
    """The full result of parsing one file."""

    model_config = ConfigDict(frozen=True)

    path: str
    language: str
    symbols: list[ParsedSymbol] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    references: list[RawReference] = Field(default_factory=list)


__all__ = ["ParseResult", "ParsedSymbol", "RawReference", "ReferenceKind", "SymbolKind"]
