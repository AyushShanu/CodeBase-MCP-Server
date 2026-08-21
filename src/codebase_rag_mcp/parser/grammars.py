"""Language-name to Tree-sitter grammar resolution and parser/language caching.

`ingestion.languages` maps both `.ts` and `.tsx` to the single string
`"typescript"` -- there is no separate ingestion-level "tsx" language.
But parsing `.tsx` source with the plain `"typescript"` grammar produces
a broken parse (JSX syntax is not valid TypeScript grammar), while the
`"tsx"` grammar handles it cleanly. `resolve_grammar_name` takes an
optional `path` hint purely to route `.tsx` files to the `"tsx"` grammar
for *parsing* -- `ParseResult.language`/`Chunk.language` stay `"typescript"`
(the original ingestion string) so downstream language filters aren't
fragmented by a distinction ingestion itself doesn't make.

`tree_sitter_language_pack`'s own Python wrapper does not guarantee
memoized `Parser`/`Language` construction (it is a thin cast around a
Rust call) -- caching is enforced explicitly here via `functools.lru_cache`
so a whole-repo parse run never rebuilds a parser per file.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from typing import Final

from tree_sitter import Language, Parser
from tree_sitter_language_pack import get_language, get_parser

from codebase_rag_mcp.parser.exceptions import UnsupportedLanguageError

logger = logging.getLogger(__name__)

LANGUAGE_TO_GRAMMAR: Final[dict[str, str]] = {
    "typescript": "typescript",
    "javascript": "javascript",
    "python": "python",
}

TSX_GRAMMAR_NAME: Final[str] = "tsx"


def resolve_grammar_name(language: str, *, path: Path | None = None) -> str:
    """Return the Tree-sitter grammar-pack name for an ingestion `language`.

    Raises `UnsupportedLanguageError` if no grammar is configured for
    `language`.
    """
    if language == "typescript" and path is not None and path.suffix.lower() == ".tsx":
        return TSX_GRAMMAR_NAME
    grammar_name = LANGUAGE_TO_GRAMMAR.get(language)
    if grammar_name is None:
        raise UnsupportedLanguageError(
            f"No Tree-sitter grammar configured for language {language!r}"
        )
    return grammar_name


@cache
def _cached_parser(grammar_name: str) -> Parser:
    logger.info("constructing Tree-sitter parser for grammar %r", grammar_name)
    return get_parser(grammar_name)  # type: ignore[no-any-return]


@cache
def _cached_language(grammar_name: str) -> Language:
    return get_language(grammar_name)  # type: ignore[no-any-return]


def get_cached_parser(grammar_name: str) -> Parser:
    """Return a cached `Parser` for an already-resolved grammar name."""
    return _cached_parser(grammar_name)


def get_cached_language(grammar_name: str) -> Language:
    """Return a cached `Language` for an already-resolved grammar name."""
    return _cached_language(grammar_name)


__all__ = [
    "LANGUAGE_TO_GRAMMAR",
    "TSX_GRAMMAR_NAME",
    "get_cached_language",
    "get_cached_parser",
    "resolve_grammar_name",
]
