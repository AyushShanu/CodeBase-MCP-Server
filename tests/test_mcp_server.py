"""Tests for the MCP server (V1): mcp.server.server's four real tools.

Uses `mcp.client._memory.InMemoryTransport` + `mcp.client.session
.ClientSession` -- the SDK's own in-process transport, running the real
`lifespan`/dispatch machinery without a subprocess -- for full tool-call
integration tests. Lifespan-only behavior (startup failure, manifest
loading) is exercised more directly via `_make_lifespan` itself, since that
needs no client/session round trip at all.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import ClassVar

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.types import CallToolResult

from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing import bm25, manifest, vector
from codebase_rag_mcp.mcp import server as server_module
from codebase_rag_mcp.mcp.server import _build_server, _make_lifespan
from codebase_rag_mcp.parser.models import SymbolKind


def _chunk(
    chunk_id: str,
    *,
    file: str = "sample.py",
    symbol: str = "foo",
    start_line: int = 1,
    end_line: int = 2,
    content: str = "def foo():\n    return 1\n",
) -> Chunk:
    return Chunk(
        id=chunk_id,
        repo="",
        file=file,
        symbol=symbol,
        type=SymbolKind.FUNCTION,
        language="python",
        start_line=start_line,
        end_line=end_line,
        content=content,
    )


class _ControlledEmbeddings:
    """Deterministic embeddings keyed on exact input text -- duplicated
    from tests/test_retrieval_hybrid.py's helper of the same name, since
    this project has no shared conftest.py."""

    vectors: ClassVar[dict[str, list[float]]] = {}
    default: ClassVar[list[float]] = [0.0, 0.0, 1.0]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_ControlledEmbeddings.vectors.get(t, _ControlledEmbeddings.default) for t in texts]


@pytest.fixture(autouse=True)
def _reset_controlled_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    _ControlledEmbeddings.vectors = {}
    monkeypatch.setattr(vector, "HuggingFaceEmbeddings", _ControlledEmbeddings)
    monkeypatch.setattr(server_module, "HuggingFaceEmbeddings", _ControlledEmbeddings)


class _FakeCrossEncoder:
    """Duplicated from tests/test_reranker.py's helper of the same name."""

    instances: ClassVar[list[dict[str, object]]] = []
    calls: ClassVar[list[list[tuple[str, str]]]] = []
    score_map: ClassVar[dict[tuple[str, str], float]] = {}
    default_score: ClassVar[float] = 0.0

    def __init__(self, model_name: str, **kwargs: object) -> None:
        self.model_name = model_name
        _FakeCrossEncoder.instances.append({"model_name": model_name, **kwargs})

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        _FakeCrossEncoder.calls.append(list(pairs))
        return [
            _FakeCrossEncoder.score_map.get(pair, _FakeCrossEncoder.default_score) for pair in pairs
        ]


@pytest.fixture(autouse=True)
def _reset_fake_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCrossEncoder.instances = []
    _FakeCrossEncoder.calls = []
    _FakeCrossEncoder.score_map = {}
    _FakeCrossEncoder.default_score = 0.0
    monkeypatch.setattr(server_module, "CrossEncoder", _FakeCrossEncoder)


_SAMPLE_PY_SOURCE = (
    "class PQueue:\n"
    "    def pause(self):\n"
    "        pass\n"
    "\n"
    "    def add(self, task):\n"
    "        return task()\n"
    "\n"
    "\n"
    "def helper():\n"
    "    return 42\n"
)


def _build_real_index(
    tmp_path: Path,
    chunks: list[Chunk],
    *,
    file_contents: dict[str, str] | None = None,
    write_manifest: bool = True,
) -> Path:
    """Build real vector + BM25 indexes for `chunks` under `tmp_path/idx`,
    optionally writing real files under `tmp_path/repo` (`file_contents`
    maps a repo-relative path to the *whole file's* real literal text --
    deliberately independent of any individual chunk's own fragment
    `.content`, since multiple chunks commonly share one `.file`) and a
    manifest pointing at that root. Returns the index directory.
    """
    index_dir = tmp_path / "idx"
    vector.build_index(chunks, index_dir=index_dir)
    bm25.build_index(chunks, index_dir=index_dir)

    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    for file, content in (file_contents or {}).items():
        path = repo_root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if write_manifest:
        manifest.write_manifest(index_dir, repo_root=repo_root, source="test")

    return index_dir


def _pqueue_chunks() -> list[Chunk]:
    return [
        _chunk(
            "pqueue-class",
            symbol="PQueue",
            start_line=1,
            end_line=6,
            content="class PQueue:\n    def pause(self):\n        pass\n\n    def add(self, task):\n        return task()\n",
        ),
        _chunk(
            "pqueue-pause",
            symbol="PQueue.pause",
            start_line=2,
            end_line=3,
            content="    def pause(self):\n        pass\n",
        ),
        _chunk(
            "helper",
            symbol="helper",
            start_line=9,
            end_line=10,
            content="def helper():\n    return 42\n",
        ),
    ]


def _map_embeddings_for_pause_query(query: str, chunks: list[Chunk]) -> None:
    """Give `query` and the PQueue/PQueue.pause chunks matching vectors (and
    `helper` an orthogonal one), so the vector side reliably surfaces a
    nonzero-score match regardless of BM25's own idf behavior on a tiny
    3-document corpus (the same zero-idf edge case documented in
    tests/test_retrieval_hybrid.py, worked around there with a distractor
    chunk)."""
    _ControlledEmbeddings.vectors = {query: [1.0, 0.0, 0.0]}
    for chunk in chunks:
        _ControlledEmbeddings.vectors[chunk.content] = (
            [1.0, 0.0, 0.0] if chunk.symbol in ("PQueue", "PQueue.pause") else [0.0, 1.0, 0.0]
        )


# --- tool registration -------------------------------------------------------------- #


def test_server_advertises_exactly_the_four_v1_tools_no_ping(tmp_path: Path) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    server = _build_server(index_dir=index_dir)

    tools = asyncio.run(server.list_tools())

    assert {t.name for t in tools} == {"search_code", "find_symbol", "get_file_context", "ask"}


# --- lifespan --------------------------------------------------------------------- #


def test_lifespan_raises_index_not_available_error_when_index_dir_is_empty(
    tmp_path: Path,
) -> None:
    from codebase_rag_mcp.mcp.exceptions import IndexNotAvailableError

    lifespan = _make_lifespan(index_dir=tmp_path / "nothing-here")
    server = _build_server(index_dir=tmp_path / "nothing-here")

    async def _run() -> None:
        async with lifespan(server):
            pass

    with pytest.raises(IndexNotAvailableError, match="codebase-rag index"):
        asyncio.run(_run())


def test_lifespan_succeeds_with_only_vector_index_present(tmp_path: Path) -> None:
    chunks = _pqueue_chunks()
    index_dir = tmp_path / "idx"
    vector.build_index(chunks, index_dir=index_dir)
    lifespan = _make_lifespan(index_dir=index_dir)
    server = _build_server(index_dir=index_dir)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.vector_index is not None
            assert state.bm25_index is None

    asyncio.run(_run())


def test_lifespan_succeeds_with_only_bm25_index_present(tmp_path: Path) -> None:
    chunks = _pqueue_chunks()
    index_dir = tmp_path / "idx"
    bm25.build_index(chunks, index_dir=index_dir)
    lifespan = _make_lifespan(index_dir=index_dir)
    server = _build_server(index_dir=index_dir)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.vector_index is None
            assert state.bm25_index is not None
            assert state.embeddings is None

    asyncio.run(_run())


def test_lifespan_loads_manifest_when_present(tmp_path: Path) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    lifespan = _make_lifespan(index_dir=index_dir)
    server = _build_server(index_dir=index_dir)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.manifest is not None
            assert state.manifest.repo_root == str((tmp_path / "repo").resolve())

    asyncio.run(_run())


def test_lifespan_manifest_is_none_when_absent(tmp_path: Path) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks(), write_manifest=False)
    lifespan = _make_lifespan(index_dir=index_dir)
    server = _build_server(index_dir=index_dir)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.manifest is None

    asyncio.run(_run())


# --- search_code / find_symbol / get_file_context / ask (via ClientSession) --------- #


async def _call(server: object, name: str, arguments: dict[str, object]) -> CallToolResult:
    async with (
        InMemoryTransport(server) as (read, write),  # type: ignore[arg-type]
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        return result


def test_search_code_returns_ranked_hits_via_client_session(tmp_path: Path) -> None:
    chunks = _pqueue_chunks()
    _map_embeddings_for_pause_query("pause", chunks)
    index_dir = _build_real_index(tmp_path, chunks)
    _FakeCrossEncoder.score_map = {("pause", c.content): 1.0 for c in chunks}
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(_call(server, "search_code", {"query": "pause"}))

    assert not result.is_error
    assert result.structured_content["query"] == "pause"
    hits = result.structured_content["hits"]
    assert hits
    assert any(h["symbol"] == "PQueue.pause" for h in hits)


def test_find_symbol_exact_match_returns_definition_chunk(tmp_path: Path) -> None:
    chunks = _pqueue_chunks()
    index_dir = _build_real_index(tmp_path, chunks)
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(_call(server, "find_symbol", {"symbol": "PQueue"}))

    assert not result.is_error
    matches = result.structured_content["matches"]
    assert len(matches) == 1
    assert matches[0]["symbol"] == "PQueue"
    assert matches[0]["score"] == 1.0


def test_find_symbol_bare_name_matches_qualified_suffix(tmp_path: Path) -> None:
    chunks = _pqueue_chunks()
    index_dir = _build_real_index(tmp_path, chunks)
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(_call(server, "find_symbol", {"symbol": "pause"}))

    assert not result.is_error
    matches = result.structured_content["matches"]
    assert len(matches) == 1
    assert matches[0]["symbol"] == "PQueue.pause"
    assert matches[0]["score"] == 0.5


def test_find_symbol_nonexistent_symbol_returns_empty_matches_not_error(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(_call(server, "find_symbol", {"symbol": "does_not_exist"}))

    assert not result.is_error
    assert result.structured_content["matches"] == []


def test_get_file_context_returns_exact_verbatim_lines_for_a_real_range(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(
        tmp_path, _pqueue_chunks(), file_contents={"sample.py": _SAMPLE_PY_SOURCE}
    )
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(server, "get_file_context", {"file": "sample.py", "start_line": 1, "end_line": 3})
    )

    assert not result.is_error
    assert (
        result.structured_content["content"] == "class PQueue:\n    def pause(self):\n        pass"
    )


def test_get_file_context_clamps_end_line_beyond_file_length(tmp_path: Path) -> None:
    index_dir = _build_real_index(
        tmp_path, _pqueue_chunks(), file_contents={"sample.py": _SAMPLE_PY_SOURCE}
    )
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(server, "get_file_context", {"file": "sample.py", "start_line": 9, "end_line": 999})
    )

    assert not result.is_error
    assert result.structured_content["end_line"] == 10


def test_get_file_context_raises_invalid_line_range_error_for_start_greater_than_end(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(
        tmp_path, _pqueue_chunks(), file_contents={"sample.py": _SAMPLE_PY_SOURCE}
    )
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(server, "get_file_context", {"file": "sample.py", "start_line": 5, "end_line": 1})
    )

    assert result.is_error


def test_get_file_context_rejects_plain_dot_dot_traversal_outside_repo_root(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(
        tmp_path, _pqueue_chunks(), file_contents={"sample.py": _SAMPLE_PY_SOURCE}
    )
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(
            server,
            "get_file_context",
            {"file": "../../../etc/passwd", "start_line": 1, "end_line": 1},
        )
    )

    assert result.is_error
    assert "resolves outside repo_root" in str(result.content)


def test_get_file_context_rejects_symlink_escape_outside_repo_root(tmp_path: Path) -> None:
    index_dir = _build_real_index(
        tmp_path, _pqueue_chunks(), file_contents={"sample.py": _SAMPLE_PY_SOURCE}
    )
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    (tmp_path / "repo" / "escape.py").symlink_to(outside)
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(server, "get_file_context", {"file": "escape.py", "start_line": 1, "end_line": 1})
    )

    assert result.is_error
    assert "resolves outside repo_root" in str(result.content)


def test_get_file_context_raises_repo_root_unknown_error_when_no_manifest(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks(), write_manifest=False)
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(
        _call(server, "get_file_context", {"file": "sample.py", "start_line": 1, "end_line": 1})
    )

    assert result.is_error
    assert "no manifest found" in str(result.content)


def test_ask_raises_clear_error_when_no_provider_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_var in (
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "LOCAL_MODEL_NAME",
    ):
        monkeypatch.delenv(env_var, raising=False)
    chunks = _pqueue_chunks()
    _map_embeddings_for_pause_query("what does pause do", chunks)
    index_dir = _build_real_index(tmp_path, chunks)
    _FakeCrossEncoder.score_map = {("what does pause do", c.content): 1.0 for c in chunks}
    server = _build_server(index_dir=index_dir)

    result = asyncio.run(_call(server, "ask", {"query": "what does pause do"}))

    assert result.is_error
    assert "No LLM provider is configured" in str(result.content)


# --- once-only construction (Day 08's caching goal) --------------------------------- #


def test_two_search_code_calls_plus_one_ask_call_load_each_dependency_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_var in (
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "LOCAL_MODEL_NAME",
    ):
        monkeypatch.delenv(env_var, raising=False)
    chunks = _pqueue_chunks()
    _map_embeddings_for_pause_query("pause", chunks)
    index_dir = _build_real_index(tmp_path, chunks)
    _FakeCrossEncoder.score_map = {("pause", c.content): 1.0 for c in chunks}

    vector_load_calls: list[int] = []
    bm25_load_calls: list[int] = []
    real_vector_load = vector.load_index
    real_bm25_load = bm25.load_index

    def _counting_vector_load(*args: object, **kwargs: object) -> object:
        vector_load_calls.append(1)
        return real_vector_load(*args, **kwargs)  # type: ignore[arg-type]

    def _counting_bm25_load(*args: object, **kwargs: object) -> object:
        bm25_load_calls.append(1)
        return real_bm25_load(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(server_module.vector, "load_index", _counting_vector_load)
    monkeypatch.setattr(server_module.bm25, "load_index", _counting_bm25_load)

    server = _build_server(index_dir=index_dir)

    async def _run() -> None:
        async with (
            InMemoryTransport(server) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            await session.call_tool("search_code", {"query": "pause"})
            await session.call_tool("search_code", {"query": "pause again"})
            await session.call_tool("ask", {"query": "pause"})

    asyncio.run(_run())

    assert len(vector_load_calls) == 1
    assert len(bm25_load_calls) == 1
    assert len(_FakeCrossEncoder.instances) == 1


# --- stdout discipline -------------------------------------------------------------- #


def test_env_vars_for_stdout_suppression_are_set_on_module_import() -> None:
    assert os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS") == "1"
    assert os.environ.get("TRANSFORMERS_VERBOSITY") == "error"
    assert os.environ.get("TOKENIZERS_PARALLELISM") == "false"


_run_real = pytest.mark.skipif(
    os.getenv("RUN_REAL_EMBEDDING_TESTS") != "1",
    reason="downloads real models on first run; opt-in via RUN_REAL_EMBEDDING_TESTS=1",
)


@_run_real
def test_real_model_server_startup_and_first_call_write_nothing_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The literal condition this guards against (a genuinely cold,
    uncached HF/sentence-transformers model) can't be forced by a portable
    unit test -- this is the automated half of that check (proving no
    stdout leak with the real libraries wired in, whatever their cache
    state happens to be locally); the Definition of Done's own manual step
    (run this against a machine with no warm cache at all) is what actually
    exercises the cold-cache condition -- see DECISIONS.md D-023.
    """
    monkeypatch.undo()  # restore the real HuggingFaceEmbeddings/CrossEncoder
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    server = _build_server(index_dir=index_dir)

    capfd.readouterr()  # discard any noise from building the index above

    asyncio.run(_call(server, "search_code", {"query": "pause"}))

    captured = capfd.readouterr()
    assert captured.out == ""


# --- asyncio.Lock pattern (isolated -- see DECISIONS.md D-023) --------------------- #


def test_shared_lock_pattern_serializes_two_concurrent_holders() -> None:
    """`hybrid_search`/`rerank` are fully synchronous (no internal `await`),
    so a tool coroutine's `async with state.lock: <synchronous call>` cannot
    exhibit interleaving with or without the lock -- there is no `await`
    point inside the critical section for the scheduler to switch tasks at.
    This test isolates the exact locking *pattern* `search_code`/`ask` use,
    with a stand-in that has a genuine internal `await`, to prove the lock
    itself works correctly -- see DECISIONS.md D-023 for why this is the
    only way to meaningfully test it today.
    """
    lock = asyncio.Lock()
    events: list[tuple[str, str]] = []

    async def _locked_critical_section(tag: str) -> None:
        async with lock:
            events.append((tag, "start"))
            await asyncio.sleep(0.05)
            events.append((tag, "end"))

    async def _run() -> None:
        await asyncio.gather(_locked_critical_section("a"), _locked_critical_section("b"))

    asyncio.run(_run())

    assert events in (
        [("a", "start"), ("a", "end"), ("b", "start"), ("b", "end")],
        [("b", "start"), ("b", "end"), ("a", "start"), ("a", "end")],
    )
