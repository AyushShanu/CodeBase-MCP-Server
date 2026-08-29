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
from codebase_rag_mcp.parser.models import (
    ParsedSymbol,
    ParseResult,
    RawReference,
    ReferenceKind,
    SymbolKind,
)

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

    # Second, independent walk over the SAME already-parsed tree -- no
    # second parser.parse() call. Deliberately a full-tree recursive walk
    # (unlike the symbol walk above, which stops at nested function/class
    # boundaries by design) since calls happen inside function bodies the
    # symbol walk never descends into. A distinct log-message prefix keeps
    # the two failure modes distinguishable in parse_errors/logs.
    references: list[RawReference] = []
    extract_refs = _REFERENCE_EXTRACTORS.get(grammar_name)
    if extract_refs is not None:
        try:
            extract_refs(tree.root_node, references)
        except Exception as exc:
            msg = f"{path}: reference extraction error: {exc}"
            parse_errors.append(msg)
            logger.warning(msg)

    return ParseResult(
        path=path.as_posix(),
        language=language,
        symbols=symbols,
        parse_errors=parse_errors,
        references=references,
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


# --- reference extraction (calls + imports) ---------------------------------- #
#
# A second, independent walk per language family, run against the same
# already-parsed tree `parse_file` already has -- never a second
# `parser.parse(source)` call. Unlike `_dispatch_ts_js`/`_dispatch_python`
# above (a targeted, stop-early walk that deliberately never recurses into
# function/method bodies, keeping nested helpers/callbacks out of the
# *symbol* list), every function below recurses into EVERY named child
# unconditionally -- calls happen inside function bodies, so the reference
# walk needs the opposite recursion policy. Node/field shapes below were
# empirically verified against this repo's installed
# `tree_sitter_language_pack` (Python 3.14, tree-sitter>=0.23,<0.26) before
# writing this -- re-verify if the pinned grammar version ever changes.


def _text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


# --- TypeScript / TSX / JavaScript reference walk ---------------------------- #


def _extract_references_ts_js(root: Node, refs: list[RawReference]) -> None:
    _walk_ts_js_references(root, refs)


def _walk_ts_js_references(node: Node, refs: list[RawReference]) -> None:
    if node.type == "call_expression":
        ref = _ts_js_call_reference(node)
        if ref is not None:
            refs.append(ref)
    elif node.type == "import_statement":
        ref = _ts_js_import_reference(node)
        if ref is not None:
            refs.append(ref)
    for child in node.named_children:
        _walk_ts_js_references(child, refs)


def _ts_js_call_reference(node: Node) -> RawReference | None:
    # `new Widget()` is a separate node type (`new_expression`), never
    # `call_expression` -- deliberately not captured here.
    func = node.child_by_field_name("function")
    if func is None:
        return None
    if func.type == "identifier":
        name = _text(func)
    elif func.type == "member_expression":
        # `obj.method()`/`a.b.c()` -- the grammar already isolates the
        # trailing component via the "property" field, no manual
        # chain-walking needed.
        prop = func.child_by_field_name("property")
        if prop is None or prop.text is None:
            return None
        name = _text(prop)
    else:
        # e.g. `foo()()` or a subscript-called value -- unrecognized
        # callee shape, never raises, just skipped.
        return None
    if not name:
        return None
    return RawReference(name=name, kind=ReferenceKind.CALL, line=node.start_point.row + 1)


def _ts_js_import_reference(node: Node) -> RawReference | None:
    source = node.child_by_field_name("source")
    if source is None:
        return None
    module = _string_literal_text(source)
    if not module:
        return None
    name = module.rstrip("/").rsplit("/", 1)[-1]
    return RawReference(
        name=name, kind=ReferenceKind.IMPORT, line=node.start_point.row + 1, module=module
    )


def _string_literal_text(string_node: Node) -> str | None:
    for child in string_node.named_children:
        if child.type == "string_fragment" and child.text is not None:
            return _text(child)
    if string_node.text is None:
        return None
    raw = _text(string_node)
    if len(raw) >= 2 and raw[0] in "'\"`":
        return raw[1:-1]
    return raw or None


# --- Python reference walk ----------------------------------------------- #


def _extract_references_python(root: Node, refs: list[RawReference]) -> None:
    _walk_python_references(root, refs)


def _walk_python_references(node: Node, refs: list[RawReference]) -> None:
    if node.type == "call":
        ref = _python_call_reference(node)
        if ref is not None:
            refs.append(ref)
    elif node.type == "import_statement":
        refs.extend(_python_import_statement_references(node))
    elif node.type == "import_from_statement":
        ref = _python_import_from_reference(node)
        if ref is not None:
            refs.append(ref)
    for child in node.named_children:
        _walk_python_references(child, refs)


def _python_call_reference(node: Node) -> RawReference | None:
    func = node.child_by_field_name("function")
    if func is None:
        return None
    if func.type == "identifier":
        name = _text(func)
    elif func.type == "attribute":
        # `obj.method()`/`a.b.c()` -- the grammar already isolates the
        # trailing component via the "attribute" field.
        attr = func.child_by_field_name("attribute")
        if attr is None or attr.text is None:
            return None
        name = _text(attr)
    else:
        # e.g. `handlers[key]()` (a subscript callee) -- unrecognized
        # shape, never raises, just skipped.
        return None
    if not name:
        return None
    return RawReference(name=name, kind=ReferenceKind.CALL, line=node.start_point.row + 1)


def _python_import_statement_references(node: Node) -> list[RawReference]:
    # `import a, b.c, d as e` -- one RawReference per distinct module
    # named (each is a genuinely different module, unlike
    # import_from_statement's single shared source below).
    out: list[RawReference] = []
    line = node.start_point.row + 1
    for child in node.children_by_field_name("name"):
        module_node = child.child_by_field_name("name") if child.type == "aliased_import" else child
        if module_node is None or module_node.text is None:
            continue
        module = _text(module_node)
        if not module:
            continue
        out.append(
            RawReference(
                name=module.rsplit(".", 1)[-1], kind=ReferenceKind.IMPORT, line=line, module=module
            )
        )
    return out


def _python_import_from_reference(node: Node) -> RawReference | None:
    # `from a.b import c, d as e` -- ONE reference for the whole
    # statement; every imported name shares the same source module,
    # which is all impact.analyzer's import resolution needs
    # (module-path resolution, never per-imported-name resolution).
    # `from . import x`'s module_name field is present but has type
    # `relative_import` (text "."), not `dotted_name` -- decoded as-is
    # rather than specially resolved, a deliberate, accepted V2 gap (a
    # "." module never matches anything meaningful during resolution).
    module_node = node.child_by_field_name("module_name")
    if module_node is None or module_node.text is None:
        return None
    module = _text(module_node)
    if not module:
        return None
    return RawReference(
        name=module.rsplit(".", 1)[-1],
        kind=ReferenceKind.IMPORT,
        line=node.start_point.row + 1,
        module=module,
    )


_REFERENCE_EXTRACTORS: dict[str, Callable[[Node, list[RawReference]], None]] = {
    "typescript": _extract_references_ts_js,
    "tsx": _extract_references_ts_js,
    "javascript": _extract_references_ts_js,
    "python": _extract_references_python,
}


__all__ = ["parse_file"]
