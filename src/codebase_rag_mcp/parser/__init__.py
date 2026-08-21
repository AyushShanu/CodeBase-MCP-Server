"""Parser: Tree-sitter based AST extraction for source files.

Parses a single file's bytes into a `ParseResult` -- a normalized list of
`ParsedSymbol`s (functions/classes/methods/interfaces) with exact,
1-indexed line ranges -- built on `tree-sitter-language-pack` for grammar
loading. Operates on one file at a time; repo-wide orchestration over an
ingested file list is out of scope here (see Day 04).
"""

from codebase_rag_mcp.parser.exceptions import ParseError, ParserError, UnsupportedLanguageError
from codebase_rag_mcp.parser.extractor import parse_file
from codebase_rag_mcp.parser.models import ParsedSymbol, ParseResult, SymbolKind

__all__ = [
    "ParseError",
    "ParseResult",
    "ParsedSymbol",
    "ParserError",
    "SymbolKind",
    "UnsupportedLanguageError",
    "parse_file",
]
