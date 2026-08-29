"""Tests for the parsing stage: grammar resolution and symbol extraction.

`parse_file` never touches the filesystem itself -- it only inspects the
`path` argument's suffix (for `.tsx` grammar routing) and uses it verbatim
as `ParseResult.path` -- so fixtures here are plain in-memory byte strings
passed with a bare `Path`, no `tmp_path` writes required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag_mcp.parser.exceptions import UnsupportedLanguageError
from codebase_rag_mcp.parser.extractor import parse_file
from codebase_rag_mcp.parser.grammars import get_cached_parser, resolve_grammar_name
from codebase_rag_mcp.parser.models import ReferenceKind, SymbolKind

# --- grammars: language + .tsx routing --------------------------------------- #


def test_resolve_grammar_name_routes_tsx_extension_to_tsx_grammar() -> None:
    assert resolve_grammar_name("typescript", path=Path("component.tsx")) == "tsx"
    assert resolve_grammar_name("typescript", path=Path("component.ts")) == "typescript"
    assert resolve_grammar_name("typescript") == "typescript"


def test_resolve_grammar_name_unsupported_language_raises() -> None:
    with pytest.raises(UnsupportedLanguageError):
        resolve_grammar_name("ruby")


def test_get_cached_parser_returns_same_instance_across_calls() -> None:
    assert get_cached_parser("typescript") is get_cached_parser("typescript")


# --- parse_file: TS/TSX/JS core coverage ------------------------------------- #


def test_parse_file_ts_function_class_interface() -> None:
    source = b"""function foo() {
  return 1;
}

class Widget {
  render() {
    return null;
  }
}

interface Props {
  name: string;
}
"""
    result = parse_file(Path("x.ts"), "typescript", source)

    by_name = {s.name: s for s in result.symbols}
    assert by_name["foo"].kind == SymbolKind.FUNCTION
    assert by_name["foo"].start_line == 1
    assert by_name["foo"].end_line == 3
    assert by_name["Widget"].kind == SymbolKind.CLASS
    assert by_name["Widget"].start_line == 5
    assert by_name["Widget"].end_line == 9
    assert by_name["Widget.render"].kind == SymbolKind.METHOD
    assert by_name["Widget.render"].start_line == 6
    assert by_name["Widget.render"].end_line == 8
    assert by_name["Props"].kind == SymbolKind.INTERFACE
    assert by_name["Props"].start_line == 11
    assert by_name["Props"].end_line == 13
    assert result.parse_errors == []


def test_parse_file_tsx_function_component_and_interface() -> None:
    source = b"""export interface Props {
  name: string;
}

export function Button(props: Props) {
  return <div>{props.name}</div>;
}
"""
    result = parse_file(Path("a.tsx"), "typescript", source)

    by_name = {s.name: s for s in result.symbols}
    assert by_name["Props"].kind == SymbolKind.INTERFACE
    assert by_name["Button"].kind == SymbolKind.FUNCTION
    assert result.parse_errors == []


def test_parse_file_js_function_and_arrow_const() -> None:
    source = b"""function foo() {
  return 1;
}

const bar = () => {
  return 2;
};
"""
    result = parse_file(Path("x.js"), "javascript", source)

    by_name = {s.name: s for s in result.symbols}
    assert by_name["foo"].kind == SymbolKind.FUNCTION
    assert by_name["bar"].kind == SymbolKind.FUNCTION
    assert "interface" not in {s.kind for s in result.symbols}


def test_parse_file_jsx_function_component() -> None:
    source = b"""function Button(props) {
  return <div>{props.name}</div>;
}
"""
    result = parse_file(Path("a.jsx"), "javascript", source)

    assert result.parse_errors == []
    by_name = {s.name: s for s in result.symbols}
    assert by_name["Button"].kind == SymbolKind.FUNCTION


# --- parse_file: export wrapping and default naming -------------------------- #


def test_parse_file_export_wrapped_declarations_extracted_like_unwrapped() -> None:
    source = b"""export function foo() {
  return 1;
}

export default class Foo {
  render() {
    return null;
  }
}

export const handler = (req, res) => {
  res.send(1);
};

class Baz {
  render() {
    return 1;
  }
}
"""
    result = parse_file(Path("x.ts"), "typescript", source)

    by_name = {s.name: s for s in result.symbols}
    assert by_name["foo"].kind == SymbolKind.FUNCTION
    assert by_name["Foo"].kind == SymbolKind.CLASS
    assert by_name["Foo.render"].kind == SymbolKind.METHOD
    assert by_name["handler"].kind == SymbolKind.FUNCTION
    assert by_name["Baz"].kind == SymbolKind.CLASS
    assert by_name["Baz.render"].kind == SymbolKind.METHOD


@pytest.mark.parametrize(
    "source",
    [
        b"export default (req, res) => {\n  res.send(1);\n};\n",
        b"export default function () {\n  return 1;\n}\n",
    ],
)
def test_parse_file_anonymous_default_export_named_default(source: bytes) -> None:
    result = parse_file(Path("x.ts"), "typescript", source)

    assert len(result.symbols) == 1
    assert result.symbols[0].name == "default"
    assert result.symbols[0].kind == SymbolKind.FUNCTION


# --- parse_file: 1-indexed line regression ----------------------------------- #


def test_parse_file_symbol_on_first_line_reports_start_line_one() -> None:
    source = b"function firstLine() {\n  return 1;\n}\n"

    result = parse_file(Path("x.ts"), "typescript", source)

    assert result.symbols[0].start_line == 1


# --- parse_file: qualified method names -------------------------------------- #


def test_parse_file_qualified_method_names_no_collision() -> None:
    source = b"""class A {
  render() {
    return 1;
  }
}

class B {
  render() {
    return 2;
  }
}
"""
    result = parse_file(Path("x.ts"), "typescript", source)

    names = {s.name for s in result.symbols}
    assert {"A.render", "B.render"} <= names
    assert "render" not in names


# --- parse_file: error resilience -------------------------------------------- #


def test_parse_file_syntax_error_records_parse_error_and_keeps_valid_symbols() -> None:
    source = b"""function good() {
  return 1;
}

function bad( {
  return 2;
"""
    result = parse_file(Path("bad.ts"), "typescript", source)

    assert result.parse_errors != []
    assert any(s.name == "good" for s in result.symbols)


def test_parse_file_unsupported_language_raises_not_empty_result() -> None:
    with pytest.raises(UnsupportedLanguageError):
        parse_file(Path("x.rb"), "ruby", b"def foo; end")


# --- parse_file: Python ------------------------------------------------------- #


def test_parse_file_python_function_class_methods_and_decorated_method() -> None:
    source = b"""def top():
    return 1


class Widget:
    def render(self):
        return 1

    @staticmethod
    def helper():
        return 2
"""
    result = parse_file(Path("m.py"), "python", source)

    by_name = {s.name: s for s in result.symbols}
    assert by_name["top"].kind == SymbolKind.FUNCTION
    assert by_name["Widget"].kind == SymbolKind.CLASS
    assert by_name["Widget.render"].kind == SymbolKind.METHOD
    assert by_name["Widget.helper"].kind == SymbolKind.METHOD
    # Decorated method's span starts at the "@staticmethod" line, not "def".
    assert by_name["Widget.helper"].start_line == 9
    assert result.parse_errors == []


# --- parse_file: reference extraction (calls + imports) ---------------------- #


def test_parse_file_ts_extracts_call_references_bare_and_member_and_chained() -> None:
    source = b"""function outer() {
  bareCall();
  obj.method();
  a.b.c();
}
"""
    result = parse_file(Path("x.ts"), "typescript", source)

    calls = [(r.name, r.kind, r.line) for r in result.references if r.kind is ReferenceKind.CALL]
    assert ("bareCall", ReferenceKind.CALL, 2) in calls
    assert ("method", ReferenceKind.CALL, 3) in calls
    assert ("c", ReferenceKind.CALL, 4) in calls


def test_parse_file_ts_extracts_import_reference_module_and_name() -> None:
    source = b'import { foo } from "./mod";\n'
    result = parse_file(Path("x.ts"), "typescript", source)

    imports = [r for r in result.references if r.kind is ReferenceKind.IMPORT]
    assert len(imports) == 1
    assert imports[0].module == "./mod"
    assert imports[0].name == "mod"
    assert imports[0].line == 1


def test_parse_file_ts_new_expression_is_not_captured_as_a_call() -> None:
    source = b"""function outer() {
  new Widget();
}
"""
    result = parse_file(Path("x.ts"), "typescript", source)

    names = {r.name for r in result.references if r.kind is ReferenceKind.CALL}
    assert "Widget" not in names


def test_parse_file_python_extracts_call_references_bare_and_attribute_and_chained() -> None:
    source = b"""def outer():
    bare_call()
    obj.method()
    a.b.c()
"""
    result = parse_file(Path("m.py"), "python", source)

    calls = [(r.name, r.kind, r.line) for r in result.references if r.kind is ReferenceKind.CALL]
    assert ("bare_call", ReferenceKind.CALL, 2) in calls
    assert ("method", ReferenceKind.CALL, 3) in calls
    assert ("c", ReferenceKind.CALL, 4) in calls


def test_parse_file_python_extracts_import_and_import_from_references() -> None:
    source = b"""import os
import os.path
import os, sys
from foo.bar import baz
"""
    result = parse_file(Path("m.py"), "python", source)

    imports = [r for r in result.references if r.kind is ReferenceKind.IMPORT]
    modules = [(r.module, r.line) for r in imports]
    assert ("os", 1) in modules
    assert ("os.path", 2) in modules
    assert ("os", 3) in modules
    assert ("sys", 3) in modules
    assert ("foo.bar", 4) in modules
    # `import os, sys` -> two distinct references, not one.
    assert modules.count(("os", 3)) == 1


def test_parse_file_reference_extraction_never_raises_for_unrecognized_call_shape() -> None:
    source = b"""def outer():
    handlers[key]()
    normal_call()
"""
    result = parse_file(Path("m.py"), "python", source)

    assert result.parse_errors == []
    names = {r.name for r in result.references if r.kind is ReferenceKind.CALL}
    assert "normal_call" in names


def test_parse_file_reference_walk_recurses_into_function_bodies_unlike_symbol_walk() -> None:
    source = b"""def outer():
    def inner():
        nested_call()
    return inner
"""
    result = parse_file(Path("m.py"), "python", source)

    # The symbol walk deliberately excludes "inner" from result.symbols
    # (nested helpers are not top-level symbols) -- but the reference walk
    # must still find the call inside it.
    assert "inner" not in {s.name for s in result.symbols}
    assert "nested_call" in {r.name for r in result.references if r.kind is ReferenceKind.CALL}
