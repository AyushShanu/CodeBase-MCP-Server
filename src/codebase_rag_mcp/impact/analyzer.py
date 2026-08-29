"""Deterministic-evidence assembly + optional LLM narration for
`analyze_impact`.

Combines `impact.symbols`'s exact/qualified-suffix definition matching
with a repo-wide `indexing.references.ReferenceIndex` (built at index
time, Day 10) to find direct callers and importing files, then -- only
once real evidence exists -- asks `impact.explain.explain_impact` to
narrate it. Name-based matching, not full type/scope resolution, is an
explicitly accepted V2 simplification (CLAUDE.md rules out "perfect
whole-program static analysis"); the CONFIRMED-vs-LIKELY labeling exists
precisely to make that limitation visible rather than overstating
precision.
"""

from __future__ import annotations

import logging
import posixpath
import re
from pathlib import PurePosixPath
from typing import Final

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.generation.exceptions import (
    AllProvidersFailedError,
    NoProviderConfiguredError,
)
from codebase_rag_mcp.impact.explain import explain_impact
from codebase_rag_mcp.impact.models import CallerInfo, Confidence, ImpactResult, ImporterInfo
from codebase_rag_mcp.impact.symbols import (
    bare_trailing_name,
    count_distinct_definitions,
    match_symbol_chunks,
    strip_part_suffix,
)
from codebase_rag_mcp.indexing.models import FileReference
from codebase_rag_mcp.indexing.references import ReferenceIndex
from codebase_rag_mcp.mcp.models import SearchHit
from codebase_rag_mcp.parser.models import ReferenceKind, SymbolKind

logger = logging.getLogger(__name__)

# Same locally-scoped-constant convention as chunker/fallback.py's
# DEFAULT_MAX_CHUNK_LINES, not a new config.py entry -- a very common
# bare name (e.g. "run", "close", "get") could otherwise return an
# unbounded caller/importer list.
MAX_IMPACT_REFERENCES_PER_KIND: Final[int] = 50

_TEST_DIR_NAMES = frozenset({"test", "tests"})
_TEST_FILENAME_RE = re.compile(r"^(test_.+\.py|.+_test\.py|.+\.test\.ts|.+\.spec\.ts)$", re.IGNORECASE)


def is_likely_test(file: str) -> bool:
    """Path-segment/filename heuristic, never a bare substring search --
    a path component exactly `"test"`/`"tests"` (case-insensitive), OR a
    filename matching `test_*.py` / `*_test.py` / `*.test.ts` /
    `*.spec.ts`. Deliberately narrow: `"contest.py"`/`"latest_config.py"`
    are never misflagged.
    """
    path = PurePosixPath(file)
    if any(part.lower() in _TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return bool(_TEST_FILENAME_RE.match(path.name))


def _strip_extension(path: str) -> str:
    root, _ext = posixpath.splitext(path)
    return root


def _module_to_candidate_paths(module: str, importing_file: str) -> list[str]:
    """Best-effort concrete repo-relative, extension-less path
    candidate(s) `module` might resolve to.

    Empty list means "too ambiguous to build a concrete full-path
    candidate" (a bare token, e.g. Python `import os` or a bare npm
    specifier) -- the basename fallback in `_resolves_to` only applies
    in that case.

    Relative/slash-containing specifiers are extension-stripped (`_strip_
    extension`) before being returned -- real-world TS/ESM code routinely
    imports a compiled `.js` extension from `.ts` source (e.g. `import x
    from "./mod.js"` resolving to a real `mod.ts` file on disk), so
    without this the two would never compare equal against
    `target_file_no_ext` (itself already extension-stripped) in
    `_resolves_to`. Confirmed against a real case during this day's
    manual verification: p-queue's `test/basic.ts` importing `../source/
    index.js`, which must resolve to the real `source/index.ts`.
    """
    if not module:
        return []
    if module.startswith("."):
        base_dir = posixpath.dirname(importing_file)
        resolved = posixpath.normpath(posixpath.join(base_dir, module))
        return [_strip_extension(resolved.removeprefix("./"))]
    if "/" in module:
        return [_strip_extension(module.removeprefix("./"))]
    if "." in module:
        return [module.replace(".", "/")]
    return []


def _resolves_to(ref: FileReference, target_file_no_ext: str) -> bool:
    """True if IMPORT reference `ref` resolves to `target_file_no_ext`.

    Tries a full repo-relative path match FIRST (including a trailing-
    path-suffix match, to tolerate an unresolvable src-root prefix, e.g.
    Python module "pkg.auth" matching real file "src/pkg/auth.py"). Only
    when NO concrete full-path candidate could be derived at all does it
    fall back to comparing bare final path segments -- crucially, a
    concrete-but-non-matching full-path candidate is a definitive
    rejection, NOT a trigger for the basename fallback (this is what
    prevents "pkg_b/auth.py" from being wrongly flagged as an importer of
    "pkg_a/auth.py" just because both end in "auth" -- the fallback only
    fires when full-path resolution was structurally impossible, e.g. a
    bare token module). A basename-only match is still `LIKELY`, never
    promoted to `CONFIRMED`.
    """
    candidates = _module_to_candidate_paths(ref.module or "", ref.file)
    if candidates:
        return any(
            target_file_no_ext == c or target_file_no_ext.endswith("/" + c) for c in candidates
        )
    module_basename = (ref.module or "").rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    target_basename = target_file_no_ext.rsplit("/", 1)[-1]
    return bool(module_basename) and module_basename == target_basename


def _resolve_caller_symbol(ref: FileReference, chunks: list[Chunk]) -> str | None:
    """Which chunk's `[start_line, end_line]` contains `ref.line` in
    `ref.file`. `None` for a whole-file-fallback chunk (`symbol == ""`,
    `type == SymbolKind.MODULE`) or when no containing chunk exists at
    all (e.g. top-level code in a file that also has real symbols, so no
    fallback chunk was ever created) -- both are "nothing meaningful to
    report" cases, deliberately not an empty string.
    """
    for chunk in chunks:
        if chunk.file == ref.file and chunk.start_line <= ref.line <= chunk.end_line:
            if chunk.symbol == "" and chunk.type is SymbolKind.MODULE:
                return None
            return strip_part_suffix(chunk.symbol)
    return None


def analyze_impact(
    symbol: str,
    chunks: list[Chunk],
    reference_index: ReferenceIndex | None,
) -> ImpactResult:
    """Deterministic-evidence assembly: definitions (via `impact.symbols`),
    direct callers (via `reference_index.by_name`, `kind == CALL`),
    importing files (via `reference_index.imports` + `_resolves_to`),
    then an optional LLM narration.

    Zero definitions -> `has_evidence=False` immediately, no
    `reference_index` access, no LLM call at all. When there IS evidence
    but every configured LLM provider fails (or none is configured),
    `explanation` degrades to `None` -- the deterministic evidence is
    always returned regardless of the LLM step's outcome.
    """
    exact, suffix = match_symbol_chunks(symbol, chunks)
    definition_chunks = exact + suffix
    if not definition_chunks:
        return ImpactResult(
            symbol=symbol,
            definitions=[],
            callers=[],
            importers=[],
            callers_truncated=False,
            importers_truncated=False,
            explanation=None,
            has_evidence=False,
        )

    definitions = [
        SearchHit(
            file=c.file,
            symbol=c.symbol,
            language=c.language,
            start_line=c.start_line,
            end_line=c.end_line,
            content=c.content,
            score=1.0 if c in exact else 0.5,
        )
        for c in definition_chunks
    ]

    callers: list[CallerInfo] = []
    importers: list[ImporterInfo] = []
    callers_truncated = False
    importers_truncated = False

    if reference_index is not None:
        call_refs = [r for r in reference_index.by_name(symbol) if r.kind is ReferenceKind.CALL]
        distinct_defs = count_distinct_definitions(bare_trailing_name(symbol), chunks)
        confidence = Confidence.CONFIRMED if distinct_defs <= 1 else Confidence.LIKELY

        callers_truncated = len(call_refs) > MAX_IMPACT_REFERENCES_PER_KIND
        callers = [
            CallerInfo(
                file=ref.file,
                line=ref.line,
                caller_symbol=_resolve_caller_symbol(ref, chunks),
                confidence=confidence,
                is_likely_test=is_likely_test(ref.file),
            )
            for ref in call_refs[:MAX_IMPACT_REFERENCES_PER_KIND]
        ]

        target_files_no_ext = {_strip_extension(c.file) for c in definition_chunks}
        matched_importers = [
            ref
            for ref in reference_index.imports
            if any(_resolves_to(ref, tf) for tf in target_files_no_ext)
        ]
        importers_truncated = len(matched_importers) > MAX_IMPACT_REFERENCES_PER_KIND
        importers = [
            ImporterInfo(file=ref.file, line=ref.line, confidence=Confidence.LIKELY)
            for ref in matched_importers[:MAX_IMPACT_REFERENCES_PER_KIND]
        ]

    partial = ImpactResult(
        symbol=symbol,
        definitions=definitions,
        callers=callers,
        importers=importers,
        callers_truncated=callers_truncated,
        importers_truncated=importers_truncated,
        explanation=None,
        has_evidence=True,
    )

    explanation: str | None
    try:
        explanation = explain_impact(symbol, partial)
    except (NoProviderConfiguredError, AllProvidersFailedError) as exc:
        logger.warning("analyze_impact: LLM explanation unavailable for %r: %s", symbol, exc)
        explanation = None

    return ImpactResult(
        symbol=symbol,
        definitions=definitions,
        callers=callers,
        importers=importers,
        callers_truncated=callers_truncated,
        importers_truncated=importers_truncated,
        explanation=explanation,
        has_evidence=True,
    )


__all__ = ["MAX_IMPACT_REFERENCES_PER_KIND", "analyze_impact", "is_likely_test"]
