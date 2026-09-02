"""Tests for Day 12's zero-config auto-indexing: `mcp.server`'s background
auto-index build (`_run_auto_index`/`_run_auto_index_build`), its
`_ServerState.indexing_status` state machine, the `_require_index_ready`
guard wired into all six tools, and the pure `_resolve_effective_source`/
`_manifest_matches_source` helpers `_lifespan` uses to decide whether a
build is needed at all.

Kept separate from `tests/test_mcp_server.py`'s existing tool-call tests
per this project's one-module-per-concern layout. Duplicates the same
local fakes that file already duplicates (`_ControlledEmbeddings`,
`_FakeCrossEncoder`, `_build_real_index`, `_call`) since this project has
no shared `conftest.py`, plus `tests/test_indexing_incremental.py`'s
`_spy` call-counting helper.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.types import CallToolResult

from codebase_rag_mcp import config
from codebase_rag_mcp.chunker.models import Chunk
from codebase_rag_mcp.indexing import bm25, manifest, vector
from codebase_rag_mcp.indexing.manifest import IndexManifest
from codebase_rag_mcp.ingestion.models import RepoSource, RepoSourceType
from codebase_rag_mcp.mcp import server as server_module
from codebase_rag_mcp.mcp.exceptions import (
    AutoIndexError,
    IndexBuildInProgressError,
    IndexNotAvailableError,
)
from codebase_rag_mcp.mcp.server import (
    _build_server,
    _make_lifespan,
    _manifest_matches_source,
    _require_index_ready,
    _resolve_effective_source,
    _ServerState,
)
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
    """Duplicated from tests/test_mcp_server.py's helper of the same name
    -- this project has no shared conftest.py."""

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
    """Duplicated from tests/test_mcp_server.py's helper of the same name."""

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


@pytest.fixture(autouse=True)
def _reset_repo_source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`config.REPO_SOURCE` is read live (`config.REPO_SOURCE`, not copied
    into a local at import time), so patching the module attribute here is
    enough to isolate every test in this file from whatever the developer's
    own `.env` happens to set -- mirroring the real, already-documented
    `GROQ_API_KEY`-captured-as-`Final` footgun this project has hit before
    (see DECISIONS.md), just for a setting `_lifespan` reads fresh instead."""
    monkeypatch.setattr(config, "REPO_SOURCE", "")


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
    ]


def _build_real_index(
    tmp_path: Path,
    chunks: list[Chunk],
    *,
    write_manifest: bool = True,
    manifest_source: str = "test",
) -> Path:
    """Duplicated (trimmed) from tests/test_mcp_server.py's helper of the
    same name -- builds real vector + BM25 indexes for `chunks` under
    `tmp_path/idx` and, optionally, a manifest pointing at `tmp_path/repo`."""
    index_dir = tmp_path / "idx"
    vector.build_index(chunks, index_dir=index_dir)
    bm25.build_index(chunks, index_dir=index_dir)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    if write_manifest:
        manifest.write_manifest(index_dir, repo_root=repo_root, source=manifest_source)
    return index_dir


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


def _write_real_source_repo(root: Path) -> Path:
    """A real, tiny, genuinely parseable repo on disk -- used by the tests
    that exercise the actual `load_repo` + `build_all_indexes_incremental`
    background pipeline rather than a hand-built index."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text(_SAMPLE_PY_SOURCE, encoding="utf-8")
    return root


async def _call(server: object, name: str, arguments: dict[str, object]) -> CallToolResult:
    """Duplicated from tests/test_mcp_server.py's helper of the same name."""
    async with (
        InMemoryTransport(server) as (read, write),  # type: ignore[arg-type]
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        return result


def _spy(monkeypatch: pytest.MonkeyPatch, obj: object, name: str) -> list[int]:
    """Duplicated from tests/test_indexing_incremental.py's helper of the
    same name -- wraps `obj.name` with a call-counting spy that still
    calls through to the real implementation."""
    call_count: list[int] = []
    real = getattr(obj, name)

    def wrapper(*args: object, **kwargs: object) -> object:
        call_count.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, name, wrapper)
    return call_count


def _gate_build_all_indexes_incremental(
    monkeypatch: pytest.MonkeyPatch, gate: threading.Event
) -> None:
    """Makes the real `build_all_indexes_incremental` call block (in its
    own worker thread, via `asyncio.to_thread` -- never on the event loop)
    until `gate.set()` is called, then run for real. `load_repo` itself is
    never gated -- for a local path it's near-instant, so the interesting
    delay to control is always the build step."""
    real = server_module.indexing_repo.build_all_indexes_incremental

    def _gated(*args: object, **kwargs: object) -> object:
        gate.wait()
        return real(*args, **kwargs)

    monkeypatch.setattr(server_module.indexing_repo, "build_all_indexes_incremental", _gated)


def _fake_lifespan_yielding(state: _ServerState) -> object:
    @asynccontextmanager
    async def _lifespan(server: object):  # type: ignore[no-untyped-def]
        yield state

    return _lifespan


# --- _resolve_effective_source ------------------------------------------------------ #


def test_resolve_effective_source_prefers_repo_flag_over_env_and_cwd(tmp_path: Path) -> None:
    git_cwd = tmp_path / "cwd"
    git_cwd.mkdir()
    (git_cwd / ".git").mkdir()

    result = _resolve_effective_source(
        repo_flag="explicit-repo", repo_source_env="env-repo", cwd=git_cwd
    )

    assert result == "explicit-repo"


def test_resolve_effective_source_falls_back_to_env_when_no_flag(tmp_path: Path) -> None:
    result = _resolve_effective_source(repo_flag=None, repo_source_env="env-repo", cwd=tmp_path)

    assert result == "env-repo"


def test_resolve_effective_source_falls_back_to_cwd_when_git_present(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    result = _resolve_effective_source(repo_flag=None, repo_source_env="", cwd=tmp_path)

    assert result == str(tmp_path)


def test_resolve_effective_source_returns_none_when_no_flag_no_env_no_git(tmp_path: Path) -> None:
    result = _resolve_effective_source(repo_flag=None, repo_source_env="", cwd=tmp_path)

    assert result is None


# --- _manifest_matches_source --------------------------------------------------------- #


def test_manifest_matches_source_url_compares_raw_source_string_not_repo_root(
    tmp_path: Path,
) -> None:
    loaded = IndexManifest(repo_root=str(tmp_path.resolve()), source="https://example.com/repo.git")

    assert _manifest_matches_source(loaded, "https://example.com/repo.git") is True
    assert _manifest_matches_source(loaded, "https://example.com/other-repo.git") is False


def test_manifest_matches_source_local_path_compares_resolved_repo_root_not_raw_source_string(
    tmp_path: Path,
) -> None:
    loaded = IndexManifest(repo_root=str(tmp_path.resolve()), source="./some/relative/path")

    assert _manifest_matches_source(loaded, str(tmp_path)) is True


# --- _require_index_ready -------------------------------------------------------------- #


def _hand_built_state(*, indexing_status: str, indexing_error: str | None = None) -> _ServerState:
    return _ServerState(
        vector_index=None,
        bm25_index=None,
        cross_encoder=None,
        embeddings=None,
        manifest=None,
        reference_index=None,
        lock=asyncio.Lock(),
        indexing_status=indexing_status,  # type: ignore[arg-type]
        indexing_error=indexing_error,
        indexing_task=None,
    )


def test_require_index_ready_passes_when_ready() -> None:
    state = _hand_built_state(indexing_status="ready")

    asyncio.run(_require_index_ready(state))  # must not raise


def test_require_index_ready_raises_in_progress_error() -> None:
    state = _hand_built_state(indexing_status="in_progress")

    with pytest.raises(IndexBuildInProgressError, match="retry shortly"):
        asyncio.run(_require_index_ready(state))


def test_require_index_ready_raises_auto_index_error_with_stored_message() -> None:
    state = _hand_built_state(indexing_status="failed", indexing_error="boom: real cause")

    with pytest.raises(AutoIndexError, match="boom: real cause"):
        asyncio.run(_require_index_ready(state))


# --- tool call guard, via the real client session -------------------------------------- #


def test_tool_call_during_in_progress_build_raises_index_build_in_progress_error() -> None:
    state = _hand_built_state(indexing_status="in_progress")
    server = _build_server(lifespan=_fake_lifespan_yielding(state))

    result = asyncio.run(_call(server, "search_code", {"query": "anything"}))

    assert result.is_error
    assert "retry shortly" in str(result.content)


def test_tool_call_after_failed_build_raises_auto_index_error_with_underlying_message() -> None:
    state = _hand_built_state(
        indexing_status="failed", indexing_error="LocalPathNotFoundError: nope"
    )
    server = _build_server(lifespan=_fake_lifespan_yielding(state))

    result = asyncio.run(_call(server, "find_symbol", {"symbol": "anything"}))

    assert result.is_error
    assert "LocalPathNotFoundError" in str(result.content)


# --- _lifespan: fast path is unchanged -------------------------------------------------- #


def test_second_serve_against_same_repo_and_index_dir_skips_auto_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    resolved_repo_root = str((tmp_path / "repo").resolve())
    call_count = _spy(monkeypatch, server_module.indexing_repo, "build_all_indexes_incremental")

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=resolved_repo_root, auto_index=True)
    server = _build_server(index_dir=index_dir, repo_source=resolved_repo_root, auto_index=True)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "ready"
            assert state.indexing_task is None

    asyncio.run(_run())

    assert call_count == []


def test_no_auto_index_with_existing_matching_index_is_completely_unaffected(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())
    lifespan = _make_lifespan(index_dir=index_dir, auto_index=False)
    server = _build_server(index_dir=index_dir, auto_index=False)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "ready"
            assert state.vector_index is not None

    asyncio.run(_run())


# --- _lifespan: slow path never blocks -------------------------------------------------- #


def test_lifespan_yields_before_artificially_slow_background_build_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = _write_real_source_repo(tmp_path / "srcrepo")
    index_dir = tmp_path / "idx"
    gate = threading.Event()
    _gate_build_all_indexes_incremental(monkeypatch, gate)

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=str(repo_root), auto_index=True)
    server = _build_server(index_dir=index_dir, repo_source=str(repo_root), auto_index=True)

    elapsed_to_yield: list[float] = []
    status_at_entry: list[str] = []

    async def _run() -> None:
        start = time.monotonic()
        async with lifespan(server) as state:
            elapsed_to_yield.append(time.monotonic() - start)
            status_at_entry.append(state.indexing_status)
            assert state.indexing_task is not None
            gate.set()
            await state.indexing_task
            assert state.indexing_status == "ready"
            assert state.vector_index is not None

    asyncio.run(_run())

    assert status_at_entry == ["in_progress"]
    assert elapsed_to_yield[0] < 1.0  # yields near-instantly, long before the gate is released


def test_different_repo_path_reusing_index_dir_triggers_background_reindex(
    tmp_path: Path,
) -> None:
    index_dir = _build_real_index(tmp_path, _pqueue_chunks())  # manifest repo_root = tmp_path/repo
    repo_b = _write_real_source_repo(tmp_path / "repo_b")

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=str(repo_b), auto_index=True)
    server = _build_server(index_dir=index_dir, repo_source=str(repo_b), auto_index=True)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "in_progress"
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "ready"
            assert state.manifest is not None
            assert state.manifest.repo_root == str(repo_b.resolve())

    asyncio.run(_run())


def test_no_repo_flag_no_env_no_git_at_cwd_raises_index_not_available_error(
    tmp_path: Path,
) -> None:
    no_git_cwd = tmp_path / "no-git-cwd"
    no_git_cwd.mkdir()
    index_dir = tmp_path / "idx-empty"

    lifespan = _make_lifespan(index_dir=index_dir, auto_index=True, cwd=no_git_cwd)
    server = _build_server(index_dir=index_dir, auto_index=True)

    async def _run() -> None:
        async with lifespan(server):
            pass

    with pytest.raises(IndexNotAvailableError, match="codebase-rag index"):
        asyncio.run(_run())


def test_no_repo_flag_no_env_git_present_at_cwd_triggers_auto_index_against_cwd(
    tmp_path: Path,
) -> None:
    git_cwd = _write_real_source_repo(tmp_path / "git-cwd")
    (git_cwd / ".git").mkdir()
    index_dir = tmp_path / "idx-empty"

    lifespan = _make_lifespan(index_dir=index_dir, auto_index=True, cwd=git_cwd)
    server = _build_server(index_dir=index_dir, auto_index=True)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "in_progress"
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "ready"
            assert state.manifest is not None
            assert state.manifest.repo_root == str(git_cwd.resolve())

    asyncio.run(_run())


def test_explicit_repo_flag_without_git_directory_is_still_honored(tmp_path: Path) -> None:
    repo_no_git = _write_real_source_repo(tmp_path / "repo-no-git")
    index_dir = tmp_path / "idx-empty"

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=str(repo_no_git), auto_index=True)
    server = _build_server(index_dir=index_dir, repo_source=str(repo_no_git), auto_index=True)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "in_progress"
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "ready"

    asyncio.run(_run())


def test_no_auto_index_with_missing_index_raises_synchronously_even_with_valid_repo_source(
    tmp_path: Path,
) -> None:
    repo_root = _write_real_source_repo(tmp_path / "repo")
    index_dir = tmp_path / "idx-empty"

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=str(repo_root), auto_index=False)
    server = _build_server(index_dir=index_dir, repo_source=str(repo_root), auto_index=False)

    async def _run() -> None:
        async with lifespan(server):
            pass

    with pytest.raises(IndexNotAvailableError, match="codebase-rag index"):
        asyncio.run(_run())


# --- full lifecycle, via a single real client session ------------------------------------ #
#
# IMPORTANT: each `InMemoryTransport(server)`/`ClientSession` pair is its own fresh
# transport-level connection, and (confirmed by direct inspection: repeatedly opening
# fresh sessions in a polling loop here produced a fresh "auto-indexing ... scheduled in
# the background" log line -- and a fresh, never-converging background build -- on every
# single poll) `lifespan` is entered fresh per connection, not once for the server
# object's lifetime. A real `codebase-rag serve` process has exactly ONE such connection
# for its entire life (one stdio pipe to one MCP client), so multiple tool calls that must
# observe the SAME background build's progress belong inside ONE session -- exactly the
# pattern `test_two_search_code_calls_plus_one_ask_call_load_each_dependency_exactly_once`
# (tests/test_mcp_server.py) already established for the fast path. Polling via repeated
# `_call()` invocations (each opening its own session) would silently test N independent,
# unrelated background builds instead of one real one settling.


def test_background_build_completion_makes_subsequent_search_code_call_return_real_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = _write_real_source_repo(tmp_path / "srcrepo")
    index_dir = tmp_path / "idx"
    gate = threading.Event()
    _gate_build_all_indexes_incremental(monkeypatch, gate)
    server = _build_server(index_dir=index_dir, repo_source=str(repo_root), auto_index=True)

    async def _run() -> CallToolResult:
        async with (
            InMemoryTransport(server) as (read, write),  # type: ignore[arg-type]
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            first = await session.call_tool("search_code", {"query": "pause"})
            assert first.is_error
            assert "retry shortly" in str(first.content)

            gate.set()

            deadline = time.monotonic() + 5.0
            result = first
            while result.is_error and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
                result = await session.call_tool("search_code", {"query": "pause"})
            return result

    result = asyncio.run(_run())

    assert not result.is_error
    hits = result.structured_content["hits"]
    assert hits
    assert any("pause" in h["symbol"] for h in hits)


def test_tool_call_after_real_background_build_failure_surfaces_auto_index_error(
    tmp_path: Path,
) -> None:
    index_dir = tmp_path / "idx-empty"
    missing_repo = tmp_path / "does-not-exist"
    server = _build_server(index_dir=index_dir, repo_source=str(missing_repo), auto_index=True)

    async def _run() -> CallToolResult:
        async with (
            InMemoryTransport(server) as (read, write),  # type: ignore[arg-type]
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            deadline = time.monotonic() + 5.0
            result = await session.call_tool("find_symbol", {"symbol": "anything"})
            while (
                result.is_error
                and "retry shortly" in str(result.content)
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
                result = await session.call_tool("find_symbol", {"symbol": "anything"})
            return result

    result = asyncio.run(_run())

    assert result.is_error
    assert "LocalPathNotFoundError" in str(result.content)


def test_background_build_failure_sets_failed_status_and_server_stays_alive(
    tmp_path: Path,
) -> None:
    index_dir = tmp_path / "idx-empty"
    missing_repo = tmp_path / "does-not-exist"

    lifespan = _make_lifespan(index_dir=index_dir, repo_source=str(missing_repo), auto_index=True)
    server = _build_server(index_dir=index_dir, repo_source=str(missing_repo), auto_index=True)

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_status == "in_progress"
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "failed"
            assert state.indexing_error is not None
            assert "LocalPathNotFoundError" in state.indexing_error

    asyncio.run(_run())  # must not raise -- the background task's exception must not propagate


# --- temporary clone cleanup ------------------------------------------------------------- #


def test_temporary_clone_cleaned_up_after_successful_background_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_dir = _write_real_source_repo(tmp_path / "fake-clone")
    fake_repo_source = RepoSource(
        source_type=RepoSourceType.GITHUB,
        original="https://example.com/repo.git",
        root=clone_dir,
        url="https://example.com/repo.git",
        is_temporary=True,
    )
    monkeypatch.setattr(server_module.repo_loader, "load_repo", lambda source: fake_repo_source)
    index_dir = tmp_path / "idx"

    lifespan = _make_lifespan(
        index_dir=index_dir, repo_source="https://example.com/repo.git", auto_index=True
    )
    server = _build_server(
        index_dir=index_dir, repo_source="https://example.com/repo.git", auto_index=True
    )

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "ready"

    asyncio.run(_run())

    assert not clone_dir.exists()


def test_temporary_clone_cleaned_up_after_failed_background_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_dir = _write_real_source_repo(tmp_path / "fake-clone")
    fake_repo_source = RepoSource(
        source_type=RepoSourceType.GITHUB,
        original="https://example.com/repo.git",
        root=clone_dir,
        url="https://example.com/repo.git",
        is_temporary=True,
    )
    monkeypatch.setattr(server_module.repo_loader, "load_repo", lambda source: fake_repo_source)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated build failure")

    monkeypatch.setattr(server_module.indexing_repo, "build_all_indexes_incremental", _boom)
    index_dir = tmp_path / "idx"

    lifespan = _make_lifespan(
        index_dir=index_dir, repo_source="https://example.com/repo.git", auto_index=True
    )
    server = _build_server(
        index_dir=index_dir, repo_source="https://example.com/repo.git", auto_index=True
    )

    async def _run() -> None:
        async with lifespan(server) as state:
            assert state.indexing_task is not None
            await state.indexing_task
            assert state.indexing_status == "failed"

    asyncio.run(_run())

    assert not clone_dir.exists()
