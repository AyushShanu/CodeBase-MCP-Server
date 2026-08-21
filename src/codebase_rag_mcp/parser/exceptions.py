"""Typed exceptions for the Tree-sitter parsing stage.

Every error `extractor.py`/`grammars.py` can raise is a subclass of
`ParserError` so callers can catch broadly (`except ParserError`) or
narrowly (`except UnsupportedLanguageError`) as needed.
"""

from __future__ import annotations


class ParserError(Exception):
    """Base class for all parser-related errors."""


class UnsupportedLanguageError(ParserError):
    """Raised when a language has no configured Tree-sitter grammar."""


class ParseError(ParserError):
    """Raised when Tree-sitter fails to produce a usable tree for a file."""


__all__ = [
    "ParseError",
    "ParserError",
    "UnsupportedLanguageError",
]
