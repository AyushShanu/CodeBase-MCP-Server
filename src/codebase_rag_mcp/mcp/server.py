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

**Day 12 -- zero-config auto-indexing on first connect:** when `lifespan`
finds no usable index under `index_dir` (or an existing index's manifest
covers a different repo than the one now being served), it no longer just
raises `IndexNotAvailableError` -- if `auto_index` is enabled and an
effective repo source can be resolved (`_resolve_effective_source`), it
schedules the whole clone+build pipeline as one `asyncio.create_task`/
`asyncio.to_thread` background task and `yield`s server state
*immediately*, without ever awaiting that task. The MCP connection
handshake therefore never blocks on an index build, regardless of repo
size. Every tool call runs `_require_index_ready` as its first statement,
raising `IndexBuildInProgressError`/`AutoIndexError` while the build is
running/failed rather than touching still-`None` state or hanging. See
DECISIONS.md for the full rationale (observed MCP client startup
timeouts, the `.git`-presence gate on the zero-config default source, and
why an already-valid index is never auto-refreshed on a plain restart).
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
from typing import Any, Literal, NamedTuple
from urllib.parse import urlparse

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
from codebase_rag_mcp.indexing import repo as indexing_repo  # noqa: E402
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
from codebase_rag_mcp.ingestion import loader as repo_loader  # noqa: E402
from codebase_rag_mcp.mcp.exceptions import (  # noqa: E402
    AutoIndexError,
    IndexBuildInProgressError,
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


@dataclass(slots=True)
class _ServerState:
    """Cached, mostly-connection-lifetime state shared by every tool call.

    Not `frozen` (Day 12) -- background auto-indexing needs to swap in a
    freshly built `vector_index`/`bm25_index`/`cross_encoder`/`embeddings`/
    `manifest`/`reference_index` once its build completes, and to flip
    `indexing_status`/`indexing_error` as it progresses. Every mutation of
    those seven fields, and every read that must observe a consistent
    snapshot, MUST happen only while holding `lock` -- a convention this
    codebase already relies on for `hybrid_search`/`rerank` (see D-023),
    not something the type system enforces here either. `lock` itself is
    fixed for the object's lifetime.

    `embeddings` is `None` when `vector_index` is `None` -- there is
    nothing to embed a query against, so no point constructing the model.
    `cross_encoder` is `None` only while `indexing_status == "in_progress"`
    (the slow path defers its ~9.7-9.9s construction, D-021, into the
    background thread so it never blocks `lifespan`'s `yield`) -- every
    tool guarded by `_require_index_ready` may assume it is non-`None`
    once that guard passes.

    `reference_index` is loaded leniently (Day 10): its absence never
    blocks server startup, unlike `vector_index`+`bm25_index` both being
    `None`, which does raise `IndexNotAvailableError` (or, since Day 12,
    triggers a background auto-index build instead) -- `analyze_impact`
    simply degrades to "definitions only, no callers/importers" when it
    is `None`, the same way every other tool works fine without a
    manifest.

    `indexing_status`/`indexing_error` (Day 12) track a scheduled
    background auto-index build: `"ready"` means every field above is
    fully populated and safe to use; `"in_progress"` means a build is
    running and every tool call must raise `IndexBuildInProgressError`
    (via `_require_index_ready`) rather than touch the still-`None`
    fields; `"failed"` means the build raised, `indexing_error` holds the
    chained cause's message, and every tool call must raise
    `AutoIndexError`. `indexing_task` holds a strong reference to the
    `asyncio.Task` `lifespan` scheduled for that build -- `asyncio` only
    keeps a *weak* reference to a scheduled task itself, so an
    unreferenced one can be garbage-collected mid-flight; it is
    write-once at construction and read-only afterward, so it needs no
    lock protection of its own.
    """

    vector_index: VectorIndex | None
    bm25_index: Bm25Index | None
    cross_encoder: CrossEncoder | None
    embeddings: HuggingFaceEmbeddings | None
    manifest: IndexManifest | None
    reference_index: ReferenceIndex | None
    lock: asyncio.Lock
    indexing_status: Literal["ready", "in_progress", "failed"]
    indexing_error: str | None
    indexing_task: asyncio.Task[None] | None


class _AutoIndexResult(NamedTuple):
    """The six live-state fields rebuilt by a completed background
    auto-index build (Day 12), returned by `_run_auto_index_build` and
    swapped into `_ServerState` under `state.lock` by `_run_auto_index`."""

    vector_index: VectorIndex | None
    bm25_index: Bm25Index | None
    cross_encoder: CrossEncoder
    embeddings: HuggingFaceEmbeddings | None
    manifest: IndexManifest | None
    reference_index: ReferenceIndex | None


def _load_ready_indexes(
    index_dir: str | Path,
) -> tuple[VectorIndex | None, Bm25Index | None, IndexManifest | None, ReferenceIndex | None]:
    """Load whatever already exists under `index_dir`, tolerating absence
    of any piece exactly as `_lifespan`'s original inline fast-path load
    block always did.

    Extracted (Day 12) so it can be reused both for the initial fast/slow
    -path decision in `_lifespan` and, unchanged, inside a completed
    background auto-index build's reload -- the ready state ends up
    byte-identical whether the index already existed or was just built by
    this same server process.
    """
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

    loaded_manifest = manifest.load_manifest(index_dir)

    loaded_reference_index: ReferenceIndex | None = None
    try:
        loaded_reference_index = references.load_index(index_dir=index_dir)
    except ReferenceIndexLoadError as exc:
        logger.warning("reference index unavailable/corrupt under %s: %s", index_dir, exc)

    return vector_index, bm25_index, loaded_manifest, loaded_reference_index


def _construct_embeddings(vector_index: VectorIndex | None) -> HuggingFaceEmbeddings | None:
    """`None` when `vector_index` is `None` -- there is nothing to embed a
    query against, so no point constructing the model. Extracted (Day 12)
    so the fast path and a completed background build both go through
    exactly one embeddings-construction call site."""
    return (
        HuggingFaceEmbeddings(
            model_name=config.EMBEDDING_MODEL_NAME,
            encode_kwargs={"normalize_embeddings": True, "batch_size": 1},
        )
        if vector_index is not None
        else None
    )


def _resolve_effective_source(
    *, repo_flag: str | None, repo_source_env: str, cwd: str | Path | None = None
) -> str | None:
    """Resolve the repo source zero-config auto-indexing should target, or
    `None` if none can be safely inferred (Day 12).

    Precedence: an explicit `--repo` flag, then a non-empty `REPO_SOURCE`
    env var, then `cwd` itself -- but ONLY if `cwd` contains a `.git`
    directory. The `.git` gate applies to this bare-`cwd` fallback tier
    alone: an explicit `--repo`/`REPO_SOURCE` value (a local path or an
    `https://` URL) is a deliberate user instruction and is honored as-is,
    `.git` or not. Without the gate, launching `serve` from an arbitrary
    directory (a home folder, a Downloads folder) with no explicit source
    configured would silently scan and embed whatever happened to be
    there.

    `cwd` is a test seam -- production callers leave it `None`, resolving
    the real `Path.cwd()`.
    """
    if repo_flag:
        return repo_flag
    if repo_source_env:
        return repo_source_env
    real_cwd = Path(cwd) if cwd is not None else Path.cwd()
    if (real_cwd / ".git").exists():
        return str(real_cwd)
    return None


def _manifest_matches_source(loaded_manifest: IndexManifest, effective_source: str) -> bool:
    """Does `loaded_manifest` already cover `effective_source` (Day 12)?

    `IndexManifest.repo_root` is always a local, resolved path -- even for
    a URL source, since `write_manifest` is called with the *checkout*
    root (a fresh temp clone dir under `DATA_DIR/clones/`), never the URL
    itself. A URL source's `repo_root` therefore differs on every single
    clone and can never identify it, so URL sources compare on
    `manifest.source` instead (the raw original string passed to
    `load_repo`, the one stable identity a URL has). Local sources compare
    on the resolved `repo_root` instead of `.source` -- `.source` may be a
    relative string while `effective_source` is already resolved (or vice
    versa), so a raw string comparison there would be fragile; `repo_root`
    is the one field `write_manifest` always normalizes to an absolute,
    resolved path.
    """
    if urlparse(effective_source).scheme.lower() in repo_loader.ALLOWED_URL_SCHEMES:
        return loaded_manifest.source == effective_source
    return loaded_manifest.repo_root == str(Path(effective_source).resolve())


def _run_auto_index_build(source: str, index_dir: str | Path) -> _AutoIndexResult:
    """Synchronous body of a Day 12 background auto-index build -- runs
    entirely inside one `asyncio.to_thread` call from `_run_auto_index`,
    never on the event loop, so it can safely block for as long as a
    clone plus a full parse/chunk/embed pass takes.

    Mirrors `cli/main.py`'s `_run_index` exactly: `load_repo(source)` then
    `build_all_indexes_incremental(repo_source.root, index_dir=index_dir,
    repo=source)`, same argument names/order, no `force` (an auto-index
    build always benefits from the incremental chunk cache the same way a
    manual one does). `repo_source.cleanup()` runs whether the build
    succeeds or fails -- a no-op for a local source, and removal of the
    temp clone dir under `DATA_DIR/clones/` for a URL source, since the
    checkout itself is never needed again once the index (or a definitive
    failure) exists.
    """
    repo_source = repo_loader.load_repo(source)
    try:
        indexing_repo.build_all_indexes_incremental(
            repo_source.root, index_dir=index_dir, repo=source
        )
    finally:
        repo_source.cleanup()

    vector_index, bm25_index, loaded_manifest, loaded_reference_index = _load_ready_indexes(
        index_dir
    )
    cross_encoder = CrossEncoder(config.RERANKER_MODEL_NAME, max_length=config.RERANKER_MAX_LENGTH)
    embeddings = _construct_embeddings(vector_index)
    return _AutoIndexResult(
        vector_index=vector_index,
        bm25_index=bm25_index,
        cross_encoder=cross_encoder,
        embeddings=embeddings,
        manifest=loaded_manifest,
        reference_index=loaded_reference_index,
    )


async def _run_auto_index(state: _ServerState, *, source: str, index_dir: str | Path) -> None:
    """Background task (Day 12) scheduled by `_lifespan`'s slow path.

    Never awaited by `lifespan` itself -- `state.indexing_task` only holds
    a reference so the task isn't garbage-collected mid-flight. On
    success, swaps the six rebuilt live-state fields into `state` and sets
    `indexing_status="ready"`, all under `state.lock`, mirroring exactly
    what the fast path already does at startup -- no special-cased
    in-memory handoff. On ANY exception, sets `indexing_status="failed"`
    and `indexing_error` to the chained cause's message, also under
    `state.lock`, and does NOT re-raise: an unhandled exception here would
    otherwise be swallowed silently by `asyncio` (a task whose exception
    is never retrieved just logs "Task exception was never retrieved" and
    the server would look permanently stuck at `"in_progress"`), which is
    exactly the silent-failure mode `AutoIndexError` exists to prevent.
    """
    try:
        result = await asyncio.to_thread(_run_auto_index_build, source, index_dir)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        async with state.lock:
            state.indexing_status = "failed"
            state.indexing_error = message
        logger.error("auto-indexing %r under %s failed: %s", source, index_dir, message)
        return

    async with state.lock:
        state.vector_index = result.vector_index
        state.bm25_index = result.bm25_index
        state.cross_encoder = result.cross_encoder
        state.embeddings = result.embeddings
        state.manifest = result.manifest
        state.reference_index = result.reference_index
        state.indexing_status = "ready"
        state.indexing_error = None
    logger.info(
        "auto-indexing %r under %s complete: vector=%s bm25=%s manifest=%s references=%s",
        source,
        index_dir,
        result.vector_index is not None,
        result.bm25_index is not None,
        result.manifest is not None,
        result.reference_index is not None,
    )


async def _require_index_ready(state: _ServerState) -> None:
    """Guard called as the first statement of every tool function (Day 12).

    A short, self-contained `async with state.lock:` that reads and
    releases immediately -- it must fully exit before a tool's own later
    `async with state.lock:` block (e.g. inside `search_code`/`ask`)
    begins, since `asyncio.Lock` is not reentrant and both run in the same
    task/coroutine; nesting them would deadlock.
    """
    async with state.lock:
        status = state.indexing_status
        error = state.indexing_error
    if status == "in_progress":
        raise IndexBuildInProgressError(
            "Auto-indexing is still building this repo's index; retry shortly."
        )
    if status == "failed":
        raise AutoIndexError(error or "Auto-indexing failed for an unknown reason.")


def _make_lifespan(
    *,
    index_dir: str | Path = config.INDEX_DIR,
    repo_source: str | None = None,
    auto_index: bool = False,
    cwd: str | Path | None = None,
) -> Callable[[MCPServer[_ServerState]], AbstractAsyncContextManager[_ServerState]]:
    """Build a `lifespan` closure over `index_dir` (and, since Day 12,
    zero-config auto-indexing's `repo_source`/`auto_index`/`cwd`).

    A factory rather than a bare module-level function so tests can point
    it at a `tmp_path` index without touching `config.INDEX_DIR` globally.

    `auto_index` defaults to `False` here (NOT `config.AUTO_INDEX`)
    deliberately: this project's own checkout -- which every test in this
    repo runs from -- has a `.git` directory, so if this default were
    `True`, every pre-Day-12 test that calls `_make_lifespan(index_dir=
    ...)`/`_build_server(index_dir=...)` without a `repo_source` would
    resolve `effective_source` to the real project root via the
    `.git`-at-cwd fallback and get misrouted into the new background-build
    path the moment its own `tmp_path`-built manifest fails to match. Only
    `run()`, the real CLI entrypoint, carries the actual
    `config.AUTO_INDEX`-derived production default. `cwd` is a test seam,
    threaded straight through to `_resolve_effective_source`.
    """

    @asynccontextmanager
    async def _lifespan(server: MCPServer[_ServerState]) -> AsyncIterator[_ServerState]:
        vector_index, bm25_index, loaded_manifest, loaded_reference_index = _load_ready_indexes(
            index_dir
        )

        effective_source: str | None = None
        needs_background_build = False

        if vector_index is None and bm25_index is None:
            # No usable index at all: this is a cache-miss, auto-index's
            # primary trigger. `auto_index=False` (or no resolvable
            # source) preserves today's exact synchronous failure.
            if auto_index:
                effective_source = _resolve_effective_source(
                    repo_flag=repo_source, repo_source_env=config.REPO_SOURCE, cwd=cwd
                )
            if effective_source is None:
                raise IndexNotAvailableError(
                    f"no index found under {index_dir!r} -- run 'codebase-rag index <repo>' first"
                )
            needs_background_build = True
        elif auto_index and loaded_manifest is not None:
            # An index exists: auto-index is a fallback, never a forced
            # rebuild -- only trigger when the existing manifest is known
            # to cover a DIFFERENT repo than the one now being served. No
            # manifest at all (a legacy/pre-manifest index) is tolerated
            # exactly as before Day 12, since there is nothing to compare.
            effective_source = _resolve_effective_source(
                repo_flag=repo_source, repo_source_env=config.REPO_SOURCE, cwd=cwd
            )
            if effective_source is not None and not _manifest_matches_source(
                loaded_manifest, effective_source
            ):
                needs_background_build = True

        if needs_background_build:
            assert effective_source is not None  # guaranteed by the branches above
            state = _ServerState(
                vector_index=None,
                bm25_index=None,
                cross_encoder=None,
                embeddings=None,
                manifest=None,
                reference_index=None,
                lock=asyncio.Lock(),
                indexing_status="in_progress",
                indexing_error=None,
                indexing_task=None,
            )
            task = asyncio.create_task(
                _run_auto_index(state, source=effective_source, index_dir=index_dir)
            )
            state.indexing_task = task
            logger.info(
                "codebase-rag-mcp starting: auto-indexing %r under %s in the background",
                effective_source,
                index_dir,
            )
            yield state
            return

        embeddings = _construct_embeddings(vector_index)
        cross_encoder = CrossEncoder(
            config.RERANKER_MODEL_NAME, max_length=config.RERANKER_MAX_LENGTH
        )
        state = _ServerState(
            vector_index=vector_index,
            bm25_index=bm25_index,
            cross_encoder=cross_encoder,
            embeddings=embeddings,
            manifest=loaded_manifest,
            reference_index=loaded_reference_index,
            lock=asyncio.Lock(),
            indexing_status="ready",
            indexing_error=None,
            indexing_task=None,
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
    repo_source: str | None = None,
    auto_index: bool = False,
) -> MCPServer[_ServerState]:
    """Construct a fresh `MCPServer` with all six tools registered (V1's
    four, Day 10's `analyze_impact`, Day 11's `repository_summary`), each
    guarded by `_require_index_ready` (Day 12) as its first statement.

    `index_dir`/`lifespan` are test seams: real callers (`run()`) use the
    defaults; tests either point `index_dir` at a `tmp_path` index (real
    lifespan, real tiny indexes, fake embeddings/`CrossEncoder`) or override
    `lifespan` entirely with a fake that yields a hand-built `_ServerState`
    (no real model/index I/O at all). `repo_source`/`auto_index` (Day 12)
    are forwarded into `_make_lifespan` only when `lifespan` isn't
    overridden -- like `index_dir`, they're ignored when a caller supplies
    its own `lifespan`. `auto_index` defaults to `False` here, not
    `config.AUTO_INDEX` -- see `_make_lifespan`'s own docstring for why;
    `run()` alone carries the real production default.
    """
    server: MCPServer[_ServerState] = MCPServer(
        SERVER_NAME,
        version=_VERSION,
        lifespan=lifespan
        if lifespan is not None
        else _make_lifespan(index_dir=index_dir, repo_source=repo_source, auto_index=auto_index),
    )

    @server.tool()
    async def search_code(
        query: str, ctx: Context[_ServerState, Any], top_k: int = 10
    ) -> SearchCodeResult:
        """Hybrid retrieval + rerank over the indexed repository. Returns
        ranked code evidence (file/symbol/line/content/score) -- not a
        generated answer; see the `ask` tool for that."""
        state: _ServerState = ctx.request_context.lifespan_context
        await _require_index_ready(state)
        async with state.lock:
            assert state.cross_encoder is not None  # guaranteed once the guard above passes
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
        await _require_index_ready(state)
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
        await _require_index_ready(state)
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
        await _require_index_ready(state)
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
        await _require_index_ready(state)
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
        await _require_index_ready(state)
        async with state.lock:
            assert state.cross_encoder is not None  # guaranteed once the guard above passes
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


def run(
    *,
    repo_source: str | None = None,
    index_dir: str | Path = config.INDEX_DIR,
    auto_index: bool = config.AUTO_INDEX,
) -> None:
    """Synchronous entrypoint used by the CLI's `serve` subcommand.

    `auto_index` defaults to `config.AUTO_INDEX` -- this is the one call
    site where the real, environment-driven zero-config auto-indexing
    default (Day 12) actually takes effect; `_build_server`/`_make_lifespan`
    themselves default `auto_index` to `False` for test isolation (see
    their own docstrings).
    """
    logger.info("Starting %s v%s", SERVER_NAME, _VERSION)
    _build_server(index_dir=index_dir, repo_source=repo_source, auto_index=auto_index).run()


__all__ = ["SERVER_NAME", "run"]
