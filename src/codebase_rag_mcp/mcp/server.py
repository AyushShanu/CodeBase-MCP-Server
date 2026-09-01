"""MCP server (V1): exposes the RAG pipeline as real MCP tools over stdio.

Built on `mcp.server.MCPServer` (this SDK's renamed successor to `FastMCP`)
-- the previous stub used `mcp.server.Server`'s low-level `@server
.list_tools()`/`@server.call_tool()` decorators, which no longer exist in
the installed `mcp==2.0.0` (confirmed: `AttributeError`). `MCPServer`
derives both JSON input and output schema from a plain function's type
hints, including full Pydantic support, via `@server.tool()`. See
DECISIONS.md D-023 for the full migration rationale.

**Import-order / stdout discipline (read before adding any import above the
marked block below):** stdio's JSON-RPC stream lives on stdout. The ML
libraries this codebase depends on (`sentence-transformers`, `transformers`,
`huggingface_hub`) print progress bars/verbosity noise directly to stdout
on first model construction, bypassing Python's `logging` entirely. The
environment variables and `logging.basicConfig` call below MUST run before
`indexing`, `reranker`, `generation`, or `sentence_transformers` are
imported anywhere in this module -- those imports transitively trigger
`transformers`/`huggingface_hub` imports the moment a model is later
constructed. This forces the pipeline imports below the env/logging setup
to violate PEP 8's import-at-top-of-file convention (`# noqa: E402` on
each) -- this is intentional; do not "fix" it by moving them up.

Startup-time caching (the vector index, BM25 index, one `CrossEncoder`, and
one embedding-model instance, each loaded/constructed exactly once via the
`lifespan` async context manager below) is what D-020/D-021 explicitly
deferred to "Day 08's MCP server" -- D-021 measured `CrossEncoder`
construction at ~9.7-9.9s, far too slow to pay on every tool call, and
`VectorIndex.query`'s own per-call embedding-model construction was found
to trigger a real network round-trip (a Hugging Face Hub "is this model
current" check) on every single query, discovered while testing this day's
work -- also fixed here for the same reason (see DECISIONS.md D-023).
Shared cached state is guarded by a single `asyncio.Lock` for the two
pipeline calls that actually touch it (`hybrid_search`/`rerank` inside
`search_code`/`ask`) -- `get_file_context`
never touches it, and `generate_answer` (a network call, not a local model)
deliberately runs outside the lock so a slow LLM call never blocks
`search_code`/`find_symbol`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codebase_rag_mcp import config

# --- stdout/stderr discipline: MUST run before any pipeline import below --- #
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
logging.basicConfig(
    stream=sys.stderr,
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# --- end stdout/stderr discipline ------------------------------------------ #

from langchain_huggingface import HuggingFaceEmbeddings  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402
from sentence_transformers import CrossEncoder  # noqa: E402

from codebase_rag_mcp import __version__ as _VERSION  # noqa: E402, N812
from codebase_rag_mcp.chunker.models import Chunk  # noqa: E402
from codebase_rag_mcp.generation.models import GeneratedAnswer  # noqa: E402
from codebase_rag_mcp.generation.pipeline import generate_answer  # noqa: E402
from codebase_rag_mcp.impact.analyzer import analyze_impact as compute_impact  # noqa: E402
from codebase_rag_mcp.impact.models import ImpactResult  # noqa: E402
from codebase_rag_mcp.impact.summary import build_repository_summary  # noqa: E402
from codebase_rag_mcp.impact.symbols import match_symbol_chunks  # noqa: E402
from codebase_rag_mcp.indexing import bm25, manifest, references, vector  # noqa: E402
from codebase_rag_mcp.indexing.bm25 import Bm25Index  # noqa: E402
from codebase_rag_mcp.indexing.exceptions import (  # noqa: E402
    Bm25LoadError,
    Bm25NotBuiltError,
    EmptyBm25IndexError,
    EmptyIndexError,
    IndexLoadError,
    IndexNotBuiltError,
    ReferenceIndexLoadError,
)
from codebase_rag_mcp.indexing.manifest import IndexManifest  # noqa: E402
from codebase_rag_mcp.indexing.references import ReferenceIndex  # noqa: E402
from codebase_rag_mcp.indexing.vector import VectorIndex  # noqa: E402
from codebase_rag_mcp.mcp.exceptions import (  # noqa: E402
    IndexNotAvailableError,
    InvalidLineRangeError,
    PathOutsideRepoRootError,
    RepoRootUnknownError,
)
from codebase_rag_mcp.mcp.models import (  # noqa: E402
    FileContextResult,
    FindSymbolResult,
    RepositorySummaryResult,
    SearchCodeResult,
    SearchHit,
)
from codebase_rag_mcp.reranker.models import RerankedResult  # noqa: E402
from codebase_rag_mcp.reranker.rerank import rerank  # noqa: E402
from codebase_rag_mcp.retrieval.hybrid import hybrid_search  # noqa: E402

logger = logging.getLogger(__name__)

SERVER_NAME = "codebase-rag-mcp"

_BM25_UNAVAILABLE = (Bm25NotBuiltError, Bm25LoadError, EmptyBm25IndexError)
_VECTOR_UNAVAILABLE = (IndexNotBuiltError, IndexLoadError, EmptyIndexError)


@dataclass(frozen=True, slots=True)
class _ServerState:
    """Cached, connection-lifetime state shared by every tool call.

    `lock` guards the two calls that actually touch `vector_index`/
    `bm25_index`/`cross_encoder`/`embeddings` (`hybrid_search`/`rerank`,
    used by `search_code` and `ask`) -- `get_file_context` never acquires
    it (it only reads the immutable `manifest` reference), and `ask`'s
    `generate_answer` call runs outside it (a network call, not shared
    local-model state).

    `embeddings` is `None` when `vector_index` is `None` -- there is
    nothing to embed a query against, so no point constructing the model.

    `reference_index` is loaded leniently (Day 10): its absence never
    blocks server startup, unlike `vector_index`+`bm25_index` both being
    `None`, which does raise `IndexNotAvailableError` -- `analyze_impact`
    simply degrades to "definitions only, no callers/importers" when it
    is `None`, the same way every other tool works fine without a
    manifest.
    """

    vector_index: VectorIndex | None
    bm25_index: Bm25Index | None
    cross_encoder: CrossEncoder
    embeddings: HuggingFaceEmbeddings | None
    manifest: IndexManifest | None
    reference_index: ReferenceIndex | None
    lock: asyncio.Lock


def _make_lifespan(
    *, index_dir: str | Path = config.INDEX_DIR
) -> Callable[[MCPServer[_ServerState]], AbstractAsyncContextManager[_ServerState]]:
    """Build a `lifespan` closure over `index_dir`.

    A factory rather than a bare module-level function so tests can point
    it at a `tmp_path` index without touching `config.INDEX_DIR` globally.
    """

    @asynccontextmanager
    async def _lifespan(server: MCPServer[_ServerState]) -> AsyncIterator[_ServerState]:
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
            raise IndexNotAvailableError(
                f"no index found under {index_dir!r} -- run 'codebase-rag index <repo>' first"
            )

        cross_encoder = CrossEncoder(
            config.RERANKER_MODEL_NAME, max_length=config.RERANKER_MAX_LENGTH
        )
        embeddings = (
            HuggingFaceEmbeddings(
                model_name=config.EMBEDDING_MODEL_NAME,
                encode_kwargs={"normalize_embeddings": True, "batch_size": 1},
            )
            if vector_index is not None
            else None
        )
        loaded_manifest = manifest.load_manifest(index_dir)

        loaded_reference_index: ReferenceIndex | None = None
        try:
            loaded_reference_index = references.load_index(index_dir=index_dir)
        except ReferenceIndexLoadError as exc:
            logger.warning("reference index unavailable/corrupt under %s: %s", index_dir, exc)

        state = _ServerState(
            vector_index=vector_index,
            bm25_index=bm25_index,
            cross_encoder=cross_encoder,
            embeddings=embeddings,
            manifest=loaded_manifest,
            reference_index=loaded_reference_index,
            lock=asyncio.Lock(),
        )
        logger.info(
            "codebase-rag-mcp ready: vector=%s bm25=%s manifest=%s references=%s",
            vector_index is not None,
            bm25_index is not None,
            loaded_manifest is not None,
            loaded_reference_index is not None,
        )
        yield state

    return _lifespan


def _chunk_to_search_hit(chunk: Chunk, *, score: float) -> SearchHit:
    return SearchHit(
        file=chunk.file,
        symbol=chunk.symbol,
        language=chunk.language,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        content=chunk.content,
        score=score,
    )


def _reranked_result_to_search_hit(result: RerankedResult) -> SearchHit:
    return _chunk_to_search_hit(result.hybrid_result.chunk, score=result.rerank_score)


def _match_symbol(symbol: str, chunks: list[Chunk]) -> list[SearchHit]:
    """Exact + qualified-suffix symbol lookup over indexed chunks.

    `chunk.symbol == symbol` is an exact match (`score=1.0`); a bare name
    (e.g. `"pause"`) also matches a qualified name's trailing component
    (e.g. `"PQueue.pause"`, per D-013's `ClassName.method` naming) via
    `chunk.symbol.endswith(f".{symbol}")` (`score=0.5`). These scores are a
    match-quality indicator, not a retrieval relevance score -- unlike
    `search_code`'s `score`, which is a real cross-encoder logit, this is a
    deterministic lookup with no ranking model involved. Exact matches
    always sort first; both groups sort by `(file, start_line)` within
    themselves for stable output.

    Thin wrapper (Day 10) around `impact.symbols.match_symbol_chunks` --
    the actual matching logic now lives there so `analyze_impact` reuses
    exactly this same implementation rather than a second, possibly-
    diverging one. This function's own output (a flat `list[SearchHit]`)
    is unchanged byte-for-byte from before that extraction.
    """
    exact, suffix = match_symbol_chunks(symbol, chunks)
    return [_chunk_to_search_hit(c, score=1.0) for c in exact] + [
        _chunk_to_search_hit(c, score=0.5) for c in suffix
    ]


def _resolve_and_check_containment(requested_file: str, repo_root: str) -> Path:
    """Resolve `requested_file` against `repo_root`, verify containment.

    Mirrors `ingestion.scanner.scan`'s resolve-then-`relative_to`
    discipline (resolve, then catch `ValueError` from `relative_to` to
    detect an escape -- including one only visible after resolving an
    intermediate symlink) -- applied fresh here since this is separate file
    I/O at query time, not a reuse of that already-tested bulk-scan code
    path. Unlike `scan()`'s "exclude and continue," a single explicit
    request raises `PathOutsideRepoRootError` instead.

    If `requested_file` is itself an absolute path, `root_real /
    requested_file` silently discards `root_real` (a `pathlib` behavior) --
    this is safe here only because the containment check below
    re-verifies the actual resolved path regardless of how it was joined;
    an absolute path outside the repo still fails `relative_to` and is
    correctly rejected.
    """
    root_real = Path(repo_root).resolve(strict=True)
    candidate = root_real / requested_file
    resolved = candidate.resolve()  # strict=False: a missing file is a
    # separate failure mode (FileNotFoundError from a later read) than
    # "resolves outside repo_root".
    try:
        resolved.relative_to(root_real)
    except ValueError as exc:
        raise PathOutsideRepoRootError(
            f"{requested_file!r} resolves outside repo_root {root_real}"
        ) from exc
    return resolved


def _read_line_range(path: Path, start_line: int, end_line: int) -> tuple[str, int]:
    """Read `path` once and return `(content, clamped_end_line)` for the
    1-indexed, inclusive `[start_line, end_line]` range.

    Never reconstructs a range from indexed chunk content -- exact verbatim
    source lines from disk only, independent of chunk boundaries.

    Raises `InvalidLineRangeError` for `start_line < 1`, `start_line >
    end_line`, or `start_line` beyond the file's last line. `end_line`
    beyond the file's last line is clamped instead of erroring -- a
    deliberately lenient case, since a caller commonly doesn't know exactly
    how long a file is.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total_lines = len(lines)
    if start_line < 1:
        raise InvalidLineRangeError(f"start_line must be >= 1, got {start_line}")
    if start_line > end_line:
        raise InvalidLineRangeError(f"start_line ({start_line}) > end_line ({end_line})")
    if start_line > total_lines:
        raise InvalidLineRangeError(
            f"start_line {start_line} is beyond {path}'s last line ({total_lines})"
        )
    clamped_end_line = min(end_line, total_lines)
    return "\n".join(lines[start_line - 1 : clamped_end_line]), clamped_end_line


def _build_server(
    *,
    index_dir: str | Path = config.INDEX_DIR,
    lifespan: Callable[[MCPServer[_ServerState]], AbstractAsyncContextManager[_ServerState]]
    | None = None,
) -> MCPServer[_ServerState]:
    """Construct a fresh `MCPServer` with all six tools registered (V1's
    four, Day 10's `analyze_impact`, Day 11's `repository_summary`).

    `index_dir`/`lifespan` are test seams: real callers (`run()`) use the
    defaults; tests either point `index_dir` at a `tmp_path` index (real
    lifespan, real tiny indexes, fake embeddings/`CrossEncoder`) or override
    `lifespan` entirely with a fake that yields a hand-built `_ServerState`
    (no real model/index I/O at all).
    """
    server: MCPServer[_ServerState] = MCPServer(
        SERVER_NAME,
        version=_VERSION,
        lifespan=lifespan if lifespan is not None else _make_lifespan(index_dir=index_dir),
    )

    @server.tool()
    async def search_code(
        query: str, ctx: Context[_ServerState, Any], top_k: int = 10
    ) -> SearchCodeResult:
        """Hybrid retrieval + rerank over the indexed repository. Returns
        ranked code evidence (file/symbol/line/content/score) -- not a
        generated answer; see the `ask` tool for that."""
        state: _ServerState = ctx.request_context.lifespan_context
        async with state.lock:
            candidates = hybrid_search(
                query,
                top_k=config.HYBRID_CANDIDATE_POOL_SIZE,
                vector_index=state.vector_index,
                bm25_index=state.bm25_index,
                embeddings=state.embeddings,
            )
            reranked = rerank(query, candidates, top_n=top_k, cross_encoder=state.cross_encoder)
        return SearchCodeResult(
            query=query, hits=[_reranked_result_to_search_hit(r) for r in reranked]
        )

    @server.tool()
    async def find_symbol(symbol: str, ctx: Context[_ServerState, Any]) -> FindSymbolResult:
        """Find a function/class/method/interface's definition location(s)
        by indexed symbol name. Returns definitions only -- it does not
        resolve usages/callers/references; see the `analyze_impact` tool
        for that. A bare method name (e.g. 'pause') also matches a
        qualified name's trailing component (e.g. 'PQueue.pause'). A
        symbol with no matches returns an empty `matches` list, not an
        error."""
        state: _ServerState = ctx.request_context.lifespan_context
        async with state.lock:
            chunks = (
                state.vector_index.chunks
                if state.vector_index is not None
                else state.bm25_index.chunks  # type: ignore[union-attr]
            )
        return FindSymbolResult(symbol=symbol, matches=_match_symbol(symbol, chunks))

    @server.tool()
    async def analyze_impact(symbol: str, ctx: Context[_ServerState, Any]) -> ImpactResult:
        """Given a symbol name, returns its definition(s), direct callers,
        and importing files, combining find_symbol's own exact +
        qualified-suffix matching with a repo-wide reference index built
        at index time. Every caller/importer is labeled CONFIRMED (an
        unambiguous bare symbol name, repo-wide) or LIKELY (an ambiguous
        name, or an import resolved only via a basename fallback) --
        name-based matching, not full type/scope resolution, is an
        explicitly accepted V2 simplification. Includes an LLM-generated
        prose explanation only when there is at least one definition and
        a configured LLM provider succeeded; a provider failure/absence
        degrades `explanation` to None without failing the call -- the
        deterministic evidence is always returned. A symbol with zero
        definitions returns has_evidence=False and explanation=None,
        never an error."""
        state: _ServerState = ctx.request_context.lifespan_context
        async with state.lock:
            chunks = (
                state.vector_index.chunks
                if state.vector_index is not None
                else state.bm25_index.chunks  # type: ignore[union-attr]
            )
        return compute_impact(symbol, chunks, state.reference_index)

    @server.tool()
    async def repository_summary(ctx: Context[_ServerState, Any]) -> RepositorySummaryResult:
        """Deterministic repo-structure summary (language/file counts,
        distinct symbol count, top-level modules) from already-indexed
        chunk metadata, plus an optional LLM narrative. 'Top-level
        modules' is a directory-structure heuristic (first path segment
        of each indexed file), not real package-boundary resolution --
        see the response model's own docstring. Degrades `explanation` to
        None, never an error, when no LLM provider is configured or every
        configured provider fails; a repository with zero indexed chunks
        returns a zeroed result with `explanation=None` and no LLM call
        at all."""
        state: _ServerState = ctx.request_context.lifespan_context
        async with state.lock:
            chunks = (
                state.vector_index.chunks
                if state.vector_index is not None
                else state.bm25_index.chunks  # type: ignore[union-attr]
            )
        return build_repository_summary(chunks)

    @server.tool()
    async def get_file_context(
        file: str, start_line: int, end_line: int, ctx: Context[_ServerState, Any]
    ) -> FileContextResult:
        """Exact verbatim source lines for `file` in the indexed repo's
        checkout. `file` may be any real file under the checkout root, not
        restricted to files that were actually indexed/chunked (a named V1
        limitation -- full secret-file exclusion is Day 11 scope).
        `end_line` beyond the file's actual length is clamped to the last
        line rather than erroring."""
        state: _ServerState = ctx.request_context.lifespan_context
        current_manifest = state.manifest
        if current_manifest is None:
            raise RepoRootUnknownError(
                "no manifest found for this index -- rebuild it with "
                "'codebase-rag index <repo>' to enable get_file_context"
            )
        resolved_path = _resolve_and_check_containment(file, current_manifest.repo_root)
        content, clamped_end_line = _read_line_range(resolved_path, start_line, end_line)
        return FileContextResult(
            file=file, start_line=start_line, end_line=clamped_end_line, content=content
        )

    @server.tool()
    async def ask(query: str, ctx: Context[_ServerState, Any]) -> GeneratedAnswer:
        """Full RAG pipeline: retrieve, rerank, and generate a
        citation-backed answer via the configured provider fallback chain.
        Returns `has_sufficient_evidence=False` rather than a fabricated
        answer when the retrieved evidence doesn't support one."""
        state: _ServerState = ctx.request_context.lifespan_context
        async with state.lock:
            candidates = hybrid_search(
                query,
                top_k=config.HYBRID_CANDIDATE_POOL_SIZE,
                vector_index=state.vector_index,
                bm25_index=state.bm25_index,
                embeddings=state.embeddings,
            )
            reranked = rerank(query, candidates, cross_encoder=state.cross_encoder)
        return generate_answer(query, reranked)

    return server


def run() -> None:
    """Synchronous entrypoint used by the CLI's `serve` subcommand."""
    logger.info("Starting %s v%s", SERVER_NAME, _VERSION)
    _build_server().run()


__all__ = ["SERVER_NAME", "run"]
