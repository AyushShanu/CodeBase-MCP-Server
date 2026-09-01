"""Standalone benchmark runner for `benchmarks/questions.json`.

Deliberately NOT pytest-discovered (`pyproject.toml`'s `testpaths =
["tests"]` excludes `benchmarks/` by design). Imports `codebase_rag_mcp`
modules directly and calls the same functions the MCP tools call
(`hybrid_search`/`rerank`, `impact.analyzer.analyze_impact`,
`impact.summary.build_repository_summary`) rather than round-tripping
through the stdio/JSON-RPC transport -- `tests/test_mcp_server.py`'s own
`InMemoryTransport` tests already establish that call path is equivalent
to going through the real tool.

Run against a real, freshly built index:
    codebase-rag index <source>
    python benchmarks/run_benchmark.py --index-dir ./data/index

Always exits 0 -- this is an evaluation artifact, not a CI gate. A
question failing after triage is an accepted, explicitly-recorded V2 gap
(see DECISIONS.md), never a build-breaking condition.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder

from codebase_rag_mcp import config
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.impact.analyzer import analyze_impact
from codebase_rag_mcp.impact.models import ImpactResult
from codebase_rag_mcp.impact.summary import build_repository_summary
from codebase_rag_mcp.impact.symbols import strip_part_suffix
from codebase_rag_mcp.indexing import bm25, manifest, references, vector
from codebase_rag_mcp.indexing.bm25 import Bm25Index
from codebase_rag_mcp.indexing.exceptions import (
    Bm25LoadError,
    Bm25NotBuiltError,
    EmptyBm25IndexError,
    EmptyIndexError,
    IndexLoadError,
    IndexNotBuiltError,
    ReferenceIndexLoadError,
)
from codebase_rag_mcp.indexing.references import ReferenceIndex
from codebase_rag_mcp.indexing.vector import VectorIndex
from codebase_rag_mcp.mcp.models import RepositorySummaryResult, SearchHit
from codebase_rag_mcp.reranker.rerank import rerank
from codebase_rag_mcp.retrieval.hybrid import hybrid_search

logger = logging.getLogger(__name__)

QUESTIONS_PATH = Path(__file__).parent / "questions.json"
RESULTS_DIR = Path(__file__).parent / "results"

_BM25_UNAVAILABLE = (Bm25NotBuiltError, Bm25LoadError, EmptyBm25IndexError)
_VECTOR_UNAVAILABLE = (IndexNotBuiltError, IndexLoadError, EmptyIndexError)

_DEFAULT_TOOL_BY_CATEGORY: dict[str, str] = {
    "exact_symbol": "search_code",
    "semantic": "search_code",
    "structural": "search_code",
    "impact": "analyze_impact",  # overridden per-question via the "tool" field
}


@dataclass
class BenchmarkContext:
    """Mirrors `mcp.server._ServerState`'s cached fields, built
    synchronously and outside the MCP protocol -- this script calls the
    pipeline functions directly, not `mcp.server`'s tool wrappers."""

    vector_index: VectorIndex | None
    bm25_index: Bm25Index | None
    cross_encoder: CrossEncoder
    embeddings: HuggingFaceEmbeddings | None
    reference_index: ReferenceIndex | None
    chunks: list[Chunk]


@dataclass
class QuestionResult:
    id: str
    category: str
    passed: bool
    detail: str


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _load_context(index_dir: str | Path) -> BenchmarkContext:
    """Mirrors `mcp.server._make_lifespan`'s exact startup sequence
    (lenient vector/BM25 loading, one `CrossEncoder`, one embeddings
    instance, lenient reference-index loading) as a synchronous,
    non-MCP-protocol call."""
    vector_index: VectorIndex | None = None
    bm25_index: Bm25Index | None = None
    try:
        vector_index = vector.load_index(index_dir=index_dir)
    except _VECTOR_UNAVAILABLE as exc:
        logger.warning("vector index unavailable under %s: %s", index_dir, exc)
    try:
        bm25_index = bm25.load_index(index_dir=index_dir)
    except _BM25_UNAVAILABLE as exc:
        logger.warning("BM25 index unavailable under %s: %s", index_dir, exc)
    if vector_index is None and bm25_index is None:
        raise SystemExit(
            f"no index found under {index_dir!r} -- run 'codebase-rag index <repo>' first"
        )

    cross_encoder = CrossEncoder(config.RERANKER_MODEL_NAME, max_length=config.RERANKER_MAX_LENGTH)
    embeddings = (
        HuggingFaceEmbeddings(
            model_name=config.EMBEDDING_MODEL_NAME,
            encode_kwargs={"normalize_embeddings": True, "batch_size": 1},
        )
        if vector_index is not None
        else None
    )

    reference_index: ReferenceIndex | None = None
    try:
        reference_index = references.load_index(index_dir=index_dir)
    except ReferenceIndexLoadError as exc:
        logger.warning("reference index unavailable/corrupt under %s: %s", index_dir, exc)

    chunks = vector_index.chunks if vector_index is not None else bm25_index.chunks  # type: ignore[union-attr]
    return BenchmarkContext(
        vector_index=vector_index,
        bm25_index=bm25_index,
        cross_encoder=cross_encoder,
        embeddings=embeddings,
        reference_index=reference_index,
        chunks=chunks,
    )

# search function that mirrors the MCP server's `search_code` tool path, but
# without the MCP protocol -- returns a list of `SearchHit` dataclasses
def _search_hits(ctx: BenchmarkContext, query: str, *, top_k: int = 10) -> list[SearchHit]:
    candidates = hybrid_search(
        query,
        top_k=config.HYBRID_CANDIDATE_POOL_SIZE,
        vector_index=ctx.vector_index,
        bm25_index=ctx.bm25_index,
        embeddings=ctx.embeddings,
    )
    reranked = rerank(query, candidates, top_n=top_k, cross_encoder=ctx.cross_encoder)
    return [
        SearchHit(
            file=r.hybrid_result.chunk.file,
            symbol=r.hybrid_result.chunk.symbol,
            language=r.hybrid_result.chunk.language,
            start_line=r.hybrid_result.chunk.start_line,
            end_line=r.hybrid_result.chunk.end_line,
            content=r.hybrid_result.chunk.content,
            score=r.rerank_score,
        )
        for r in reranked
    ]


def score_search_question(question: dict[str, Any], hits: list[SearchHit]) -> tuple[bool, str]:
    """`exact_symbol`/`semantic`/`structural`: `expected_file` must be
    present among hit files; if `expected_symbol` is also given, a hit in
    that file must exact- or qualified-suffix-match it once any
    `chunker.fallback.split_oversized_symbol`-applied `#partN` suffix is
    stripped (`impact.symbols.strip_part_suffix`) -- an oversized symbol
    like `PQueue` legitimately chunks into `PQueue#part1`/`#part2`/...,
    and a benchmark scorer that doesn't account for that documented
    convention would wrongly fail every oversized-symbol question."""
    expected_file = question.get("expected_file")
    expected_symbol = question.get("expected_symbol")
    file_hits = [h for h in hits if h.file == expected_file]
    if not file_hits:
        return False, f"expected_file {expected_file!r} not among top hits"
    if expected_symbol:
        symbol_match = any(
            strip_part_suffix(h.symbol) == expected_symbol
            or strip_part_suffix(h.symbol).endswith(f".{expected_symbol}")
            for h in file_hits
        )
        if not symbol_match:
            return False, f"expected_symbol {expected_symbol!r} not found in {expected_file!r} hits"
    return True, "ok"


def score_analyze_impact_question(
    question: dict[str, Any], result: ImpactResult
) -> tuple[bool, str]:
    """`impact`/`analyze_impact` rows: real evidence must exist,
    `expected_file` (if given) among the definitions' files, and total
    evidence count at least `expected_min_evidence_count`."""
    if not result.has_evidence:
        return False, "has_evidence is False"
    expected_file = question.get("expected_file")
    if expected_file and expected_file not in {d.file for d in result.definitions}:
        return False, f"expected_file {expected_file!r} not among definitions"
    evidence_count = len(result.definitions) + len(result.callers) + len(result.importers)
    min_count = question.get("expected_min_evidence_count", 0)
    if evidence_count < min_count:
        return False, f"evidence_count {evidence_count} < expected_min_evidence_count {min_count}"
    return True, "ok"


def score_repository_summary_question(
    question: dict[str, Any], result: RepositorySummaryResult
) -> tuple[bool, str]:
    """`impact`/`repository_summary` rows: `expected_language` (if given)
    must appear with a positive count, and `top_level_module_count` must
    meet `expected_min_top_level_modules`."""
    expected_language = question.get("expected_language")
    if expected_language and result.languages.get(expected_language, 0) <= 0:
        return False, f"expected_language {expected_language!r} not present"
    min_modules = question.get("expected_min_top_level_modules", 0)
    if result.top_level_module_count < min_modules:
        return False, f"top_level_module_count {result.top_level_module_count} < {min_modules}"
    return True, "ok"


def run_question(question: dict[str, Any], ctx: BenchmarkContext) -> QuestionResult:
    """Dispatches on `question["tool"]` if present, else
    `_DEFAULT_TOOL_BY_CATEGORY[question["category"]]` -- the original 14
    rows (no `tool` field) default to the `search_code` path unchanged."""
    tool = question.get("tool", _DEFAULT_TOOL_BY_CATEGORY.get(question["category"], "search_code"))
    if tool == "search_code":
        hits = _search_hits(ctx, question["question"])
        passed, detail = score_search_question(question, hits)
    elif tool == "analyze_impact":
        symbol = question.get("expected_symbol") or ""
        impact_result = analyze_impact(symbol, ctx.chunks, ctx.reference_index)
        passed, detail = score_analyze_impact_question(question, impact_result)
    elif tool == "repository_summary":
        summary_result = build_repository_summary(ctx.chunks)
        passed, detail = score_repository_summary_question(question, summary_result)
    else:
        passed, detail = False, f"unknown tool {tool!r}"
    return QuestionResult(
        id=question["id"], category=question["category"], passed=passed, detail=detail
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run benchmarks/questions.json against a real indexed repository."
    )
    parser.add_argument("--index-dir", default=config.INDEX_DIR)
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    # Per the spec's explicit "confirm the index was built fresh and
    # includes a current references.json" rule -- this project has
    # independently hit a stale/wrong/incomplete index at least three
    # times before Day 11; refuse to run against a directory with no
    # manifest at all rather than silently scoring a stale index.
    if manifest.load_manifest(args.index_dir) is None:
        sys.stderr.write(
            f"no manifest found under {args.index_dir!r} -- refusing to run against a "
            "possibly stale or missing index. Run 'codebase-rag index <repo>' first.\n"
        )
        return 0

    questions = load_questions(Path(args.questions))
    ctx = _load_context(args.index_dir)

    results = [run_question(q, ctx) for q in questions]

    by_category: dict[str, list[QuestionResult]] = {}
    for result in results:
        by_category.setdefault(result.category, []).append(result)

    sys.stdout.write("Benchmark results\n")
    sys.stdout.write("=" * 60 + "\n")
    for category in sorted(by_category):
        rows = by_category[category]
        passed_count = sum(1 for r in rows if r.passed)
        sys.stdout.write(f"\n{category}: {passed_count}/{len(rows)} passed\n")
        for r in rows:
            mark = "PASS" if r.passed else "FAIL"
            sys.stdout.write(f"  [{mark}] {r.id}: {r.detail}\n")

    total_passed = sum(1 for r in results if r.passed)
    sys.stdout.write(f"\nTotal: {total_passed}/{len(results)} passed\n")

    output_path = Path(args.output) if args.output else (RESULTS_DIR / "latest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            [
                {"id": r.id, "category": r.category, "passed": r.passed, "detail": r.detail}
                for r in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    sys.stdout.write(f"\nReport written to {output_path}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
