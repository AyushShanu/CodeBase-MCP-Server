"""Tree-sitter AST parsing and structural-symbol extraction.

`parse_file` runs the Tree-sitter parser on raw bytes (byte offsets are the
authoritative position data Tree-sitter works in -- no text decoding
happens in this module, see `chunker.chunker` for the one place file bytes
become text) and then walks the resulting tree with a hand-rolled recursive
walk per language family, rather than a Tree-sitter `Query`/`QueryCursor`
or the higher-level `process()`/tags-query helpers in
`tree_sitter_language_pack`.

A hand-rolled walk was chosen because the hardest requirement here --
qualifying a nested method as `f"{class_name}.{method_name}"` -- needs
explicit "what class am I currently inside" context threaded through the
walk. `Query`/`QueryCursor` captures come back as flat, parent-context-free
node lists, so a query-based approach would still need a manual `.parent`
walk to recover that context, buying nothing on the one thing that's hard.
A plain recursive walk instead gives direct, testable control over exactly
two things: which node types cause recursion to *continue* vs *stop* (so
nested helper functions and inline callbacks like `.map(x => ...)` never
leak into the top-level symbol list), and tracking the enclosing class name
explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from tree_sitter import Node

from codebase_rag_mcp.parser.exceptions import ParseError
from codebase_rag_mcp.parser.grammars import get_cached_parser, resolve_grammar_name
from codebase_rag_mcp.parser.models import ParsedSymbol, ParseResult, SymbolKind

logger = logging.getLogger(__name__)


def parse_file(path: Path, language: str, source: bytes) -> ParseResult:
    """Parse one file's bytes into a `ParseResult`.

    Never raises for a malformed/partially-broken file -- syntax errors and
    extraction failures are recorded in `ParseResult.parse_errors` instead,
    so a single bad file can never abort a whole-repo run. Raises
    `UnsupportedLanguageError` (propagated from `resolve_grammar_name`) if
    `language` has no configured grammar/extraction logic -- that gap must
    stay visible to the caller, not silently swallowed into an empty
    result. Raises `ParseError` if Tree-sitter itself fails to produce a
    tree at all (distinct from a tree with `has_error=True`, which is a
    normal-but-degraded parse, not an exception).
    """
    grammar_name = resolve_grammar_name(language, path=path)
    parser = get_cached_parser(grammar_name)

    try:
        tree = parser.parse(source)
    except Exception as exc:
        raise ParseError(f"Tree-sitter failed to parse {path}: {exc}") from exc

    parse_errors: list[str] = []
    if tree.root_node.has_error:
        msg = f"{path}: syntax error(s) detected; parsed opportunistically"
        parse_errors.append(msg)
        logger.warning(msg)

    symbols: list[ParsedSymbol] = []
    extract = _EXTRACTORS.get(grammar_name)
    if extract is not None:
        try:
            extract(tree.root_node, symbols)
        except Exception as exc:
            msg = f"{path}: extraction error: {exc}"
            parse_errors.append(msg)
            logger.warning(msg)

    return ParseResult(
        path=path.as_posix(), language=language, symbols=symbols, parse_errors=parse_errors
    )


# --- shared helpers ---------------------------------------------------------- #


def _node_name(node: Node) -> str | None:
    """Read and decode a node's `name` field, or `None` if it has none."""
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.text is None:
        return None
    return name_node.text.decode("utf-8")


def _make_symbol(node: Node, kind: SymbolKind, name: str) -> ParsedSymbol:
    # Tree-sitter's Point.row is 0-indexed; ParsedSymbol.start_line/end_line
    # are 1-indexed to match editor/citation conventions -- +1 on both.
    # This is the single most common off-by-one when wiring up Tree-sitter.
    return ParsedSymbol(
        name=name,
        kind=kind,
        start_line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


# --- TypeScript / TSX / JavaScript ------------------------------------------- #

_TS_JS_FUNCTION_TYPES = frozenset({"function_declaration", "function_expression", "arrow_function"})
_TS_JS_CLASS_TYPES = frozenset({"class_declaration", "class"})


def _extract_ts_js(root: Node, symbols: list[ParsedSymbol]) -> None:
    for child in root.named_children:
        _dispatch_ts_js(child, symbols)


def _dispatch_ts_js(node: Node, symbols: list[ParsedSymbol]) -> None:
    if node.type == "export_statement":
        # `export function foo() {}`, `export default class Foo {}`, and
        # `export const x = () => {}` all still contain a plain declaration
        # node -- unwrap transparently by recursing into the export's
        # children instead of treating `export_statement` as a boundary.
        for child in node.named_children:
            _dispatch_ts_js(child, symbols)
        return

    if node.type in _TS_JS_FUNCTION_TYPES:
        # Anonymous default exports (`export default function () {}`) have
        # no `name` field -- name them "default" rather than dropping them.
        symbols.append(_make_symbol(node, SymbolKind.FUNCTION, _node_name(node) or "default"))
        return

    if node.type in _TS_JS_CLASS_TYPES:
        class_name = _node_name(node) or "default"
        symbols.append(_make_symbol(node, SymbolKind.CLASS, class_name))
        _extract_ts_js_methods(node, class_name, symbols)
        return

    if node.type == "interface_declaration":
        symbols.append(_make_symbol(node, SymbolKind.INTERFACE, _node_name(node) or "default"))
        return

    if node.type == "lexical_declaration":
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            value = decl.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression"):
                continue
            name_node = decl.child_by_field_name("name")
            name = name_node.text.decode("utf-8") if name_node and name_node.text else "default"
            # Span is the variable_declarator itself ("name = (...) => {...}"),
            # deliberately excluding the const/let/var keyword.
            symbols.append(_make_symbol(decl, SymbolKind.FUNCTION, name))
        return

    # Anything else (statement blocks, call arguments/callbacks, plain
    # expressions, etc.) is a leaf for our purposes -- stop, do not recurse.
    # This keeps nested helper functions and `.map(x => ...)` callbacks out
    # of the top-level symbol list.


def _extract_ts_js_methods(class_node: Node, class_name: str, symbols: list[ParsedSymbol]) -> None:
    body = class_node.child_by_field_name("body")
    if body is None:
        return
    for child in body.named_children:
        if child.type != "method_definition":
            continue
        name_node = child.child_by_field_name("name")
        method_name = name_node.text.decode("utf-8") if name_node and name_node.text else "default"
        symbols.append(_make_symbol(child, SymbolKind.METHOD, f"{class_name}.{method_name}"))


# --- Python -------------------------------------------------------------- #


def _extract_python(root: Node, symbols: list[ParsedSymbol]) -> None:
    for child in root.named_children:
        _dispatch_python(child, symbols)


def _dispatch_python(node: Node, symbols: list[ParsedSymbol]) -> None:
    target = node
    if target.type == "decorated_definition":
        inner = target.child_by_field_name("definition")
        if inner is None:
            return
        target = inner

    if target.type == "function_definition":
        # Span is the outer `node` so a `@decorator` line stays in the chunk.
        symbols.append(_make_symbol(node, SymbolKind.FUNCTION, _node_name(target) or "default"))
        return

    if target.type == "class_definition":
        class_name = _node_name(target) or "default"
        symbols.append(_make_symbol(node, SymbolKind.CLASS, class_name))
        _extract_python_methods(target, class_name, symbols)
        return

    # Module-level assignments/imports/if-guards/etc. -- skip, do not recurse.


def _extract_python_methods(class_node: Node, class_name: str, symbols: list[ParsedSymbol]) -> None:
    # `body`'s node type is "block" -- the same type a function's own body
    # has. Only ever descend into a class_definition's own `body` field,
    # never into a generic "block" node, or a method's contents would be
    # wrongly walked as more symbols.
    body = class_node.child_by_field_name("body")
    if body is None:
        return
    for child in body.named_children:
        target = child
        if target.type == "decorated_definition":
            inner = target.child_by_field_name("definition")
            if inner is None:
                continue
            target = inner
        if target.type != "function_definition":
            continue
        name_node = target.child_by_field_name("name")
        method_name = name_node.text.decode("utf-8") if name_node and name_node.text else "default"
        symbols.append(_make_symbol(child, SymbolKind.METHOD, f"{class_name}.{method_name}"))


_EXTRACTORS: dict[str, Callable[[Node, list[ParsedSymbol]], None]] = {
    "typescript": _extract_ts_js,
    "tsx": _extract_ts_js,
    "javascript": _extract_ts_js,
    "python": _extract_python,
}


__all__ = ["parse_file"]
