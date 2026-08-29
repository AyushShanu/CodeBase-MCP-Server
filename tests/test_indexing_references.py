"""Tests for the repo-wide reference/import index (indexing.references)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag_mcp.indexing.exceptions import ReferenceIndexLoadError
from codebase_rag_mcp.indexing.models import FileReference
from codebase_rag_mcp.indexing.references import build_index, load_index, write_index
from codebase_rag_mcp.parser.models import ReferenceKind


def _file_reference(
    file: str = "a.py",
    name: str = "foo",
    kind: ReferenceKind = ReferenceKind.CALL,
    line: int = 1,
    module: str | None = None,
) -> FileReference:
    return FileReference(file=file, name=name, kind=kind, line=line, module=module)


# --- build_index / write_index / load_index round trip ------------------------ #


def test_build_index_and_load_index_roundtrip_reference_fields_exactly(tmp_path: Path) -> None:
    refs = [
        _file_reference("a.py", "foo", ReferenceKind.CALL, 3),
        _file_reference("b.py", "mod", ReferenceKind.IMPORT, 1, module="pkg.mod"),
    ]
    index_dir = tmp_path / "idx"
    index = build_index(refs)
    write_index(index, index_dir=index_dir)

    loaded = load_index(index_dir=index_dir)

    assert loaded is not None
    assert loaded.references == refs


def test_build_index_never_raises_on_empty_references_list(tmp_path: Path) -> None:
    index = build_index([])
    assert index.size == 0
    write_index(index, index_dir=tmp_path / "idx")

    loaded = load_index(index_dir=tmp_path / "idx")
    assert loaded is not None
    assert loaded.size == 0


def test_load_index_returns_none_when_file_absent(tmp_path: Path) -> None:
    assert load_index(index_dir=tmp_path / "nothing-here") is None


def test_load_index_raises_reference_index_load_error_on_corrupt_json(tmp_path: Path) -> None:
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True)
    (index_dir / "references.json").write_bytes(b"not valid json")

    with pytest.raises(ReferenceIndexLoadError):
        load_index(index_dir=index_dir)


# --- ReferenceIndex.by_name / .imports ----------------------------------------- #


def test_by_name_groups_call_and_import_entries_sorted_by_file_then_line() -> None:
    refs = [
        _file_reference("b.py", "pause", ReferenceKind.CALL, 5),
        _file_reference("a.py", "pause", ReferenceKind.CALL, 2),
        _file_reference("a.py", "pause", ReferenceKind.IMPORT, 1, module="pkg.pause"),
    ]
    index = build_index(refs)

    matches = index.by_name("pause")
    assert [(r.file, r.line) for r in matches] == [("a.py", 1), ("a.py", 2), ("b.py", 5)]
    assert index.by_name("does-not-exist") == []


def test_imports_property_returns_only_import_kind_entries_sorted() -> None:
    refs = [
        _file_reference("b.py", "helper", ReferenceKind.CALL, 5),
        _file_reference("b.py", "mod2", ReferenceKind.IMPORT, 1, module="pkg.mod2"),
        _file_reference("a.py", "mod1", ReferenceKind.IMPORT, 3, module="pkg.mod1"),
    ]
    index = build_index(refs)

    imports = index.imports
    assert [(r.file, r.line) for r in imports] == [("a.py", 3), ("b.py", 1)]
    assert all(r.kind is ReferenceKind.IMPORT for r in imports)
