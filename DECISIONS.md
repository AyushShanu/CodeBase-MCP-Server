# DECISIONS

> Living log of architectural and design decisions for `codebase-rag-mcp`.
> Each entry captures the **context**, the **decision**, and the
> **consequences** — including what we are deliberately *not* doing and
> what would cause us to revisit. Keep entries short and dated.

---

## D-001 · Adopt a src-layout with hatchling

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Need an installable Python package that is also friendly
  to editors, CI, and packaging tools.
- **Decision:** Use a `src/codebase_rag_mcp/` layout built with
  `hatchling`. Tests run with `pytest` against `pythonpath = ["src"]`.
- **Consequences:**
  - Forces tests and CLI to import the installed package, catching
    packaging mistakes (missing `__init__.py`, wrong names) early.
  - `hatchling` has no extra config beyond the wheel target.
  - Revise if we need to ship native extensions or data files.

---

## D-002 · Pin Python ≥ 3.11

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Modern type-parameter syntax (`type X = ...`), `StrEnum`,
  and the official MCP SDK are most stable on 3.11+.
- **Decision:** `requires-python = ">=3.11"`.
- **Consequences:**
  - We can use `Self`, `LiteralString`, task-group cancellation, and
    PEP 695 generics without backports.
  - Drops Python 3.9 / 3.10 from the matrix.

---

## D-003 · FAISS-CPU, not FAISS-GPU

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** RAG indexing of a typical codebase (10k–100k chunks) fits
  comfortably in CPU memory; GPU FAISS would add a heavy native dep.
- **Decision:** Depend on `faiss-cpu`.
- **Consequences:**
  - No CUDA toolchain required to install or run.
  - Trade-off: slower index build for very large corpora. Revisit if
    we start indexing millions of chunks.

---

## D-004 · Hybrid retrieval (dense + BM25)

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Code search benefits from exact identifier matching
  (BM25) *and* semantic similarity (embeddings). Either alone is weak.
- **Decision:** Index with both `faiss-cpu` (dense) and `rank-bm25`
  (sparse) and combine scores in the retrieval layer.
- **Consequences:**
  - Two indexes to keep in sync; persisted together in `INDEX_DIR`.
  - Need a merge strategy (RRF, weighted sum, ...). To be decided
    when implementing `retrieval/`.

---

## D-005 · Pluggable LLM providers via httpx

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Users have keys with NVIDIA, Groq, OpenRouter, Gemini,
  or run a local OpenAI-compatible server.
- **Decision:** Expose one adapter per provider under
  `generation/providers/`, all speaking the OpenAI Chat Completions
  shape over `httpx`. Provider selection is config-driven, never
  hard-coded.
- **Consequences:**
  - Adapters stay small and uniform; easier to test in isolation.
  - Anything that doesn't speak OpenAI Chat needs a translator.

---

## D-006 · MCP transport: stdio only for now

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** MCP clients (Claude Desktop, editors, CLIs) primarily
  launch servers as child processes.
- **Decision:** Ship the stdio transport via the official
  `mcp.server.stdio.stdio_server` helper.
- **Consequences:**
  - Zero networking setup for local use.
  - SSE / HTTP transports can be added later without changing tool
    definitions — only the run loop changes.

---

## D-007 · Scaffold-first; no RAG logic yet

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Want a clean, lint-clean, type-checked, tested baseline
  before any pipeline code lands, so later commits are easy to review.
- **Decision:** Submodules are placeholder `__init__.py` files with
  docstrings describing intent. Only `config`, `cli`, and the MCP
  server stub are implemented.
- **Consequences:**
  - Easy to onboard new contributors; no half-finished logic to read.
  - The MCP server only exposes a `ping` tool today. Real tools
    (`search`, `impact`, `ask`) appear in later PRs.

---

## D-008 · Tooling: ruff + mypy + pytest in CI

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Want a single, fast, conventional toolchain.
- **Decision:**
  - **ruff** for lint and format (`ruff check`, `ruff format --check`).
  - **mypy** for static types (lenient at the package boundary while
    third-party type stubs mature; tightened per-module as we go).
  - **pytest** for tests.
  - GitHub Actions runs all four on every push.
- **Consequences:**
  - One source of truth for style and types; PRs only need to pass CI.
  - We can add `pre-commit` later if we want local enforcement.

---

## D-009 · Shallow `git clone` via `subprocess`, not GitPython

- **Date:** 2026-08-20
- **Status:** Accepted
- **Context:** Ingestion (day-02-github-ingestion) needs a working copy of
  an arbitrary GitHub repository given only a URL. Two options were
  considered: shell out to the system `git` binary via `subprocess`, or
  depend on `GitPython` for a nicer Python API around the same binary.
- **Decision:** Shell out to `git clone --depth 1 -- <url> <dest>` via
  `subprocess.run(...)` with an explicit argument list (never
  `shell=True`) and an enforced `timeout`. Reject any URL whose scheme is
  not `https` (`ssh://`, `git://`, `ext::`, `file://`, etc.) before ever
  invoking `git`. `GitPython` was rejected: it is a thin wrapper around
  the same `git` binary and buys nothing for a single shallow-clone call,
  at the cost of a new pip dependency.
- **Consequences:**
  - This is **not** a shell-injection concern — `git` is always invoked
    with an argument list, never through a shell. The real risks closed
    by the `https`-only restriction are SSRF / protocol abuse (git's
    `ssh://` and `ext::` transports can reach internal hosts or leak
    credentials via `.netrc` / an active SSH agent) and unbounded
    resource use from a hostile or slow/oversized remote — the clone
    timeout closes the latter.
  - No new pip dependency; requires a system `git` binary on `PATH`
    (already implicit for a project developed inside a git repo).
  - Clones land under `DATA_DIR/clones/<unique>` and are **not**
    automatically deleted by ingestion — cleanup is opt-in via
    `RepoSource.cleanup()`. Automatic lifecycle management (delete-
    after-index, LRU eviction of stale clones) is deliberately deferred
    to whichever later day wires ingestion into the `index`/`serve` CLI
    flow.
  - Revisit if we need `git` operations beyond a single shallow clone
    (e.g. incremental fetch/pull for re-indexing), where `GitPython`'s
    richer API might start to pay for itself.
