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

---

## D-010 · Tree-sitter grammar loading via `tree-sitter-language-pack`

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Day 03 (tree-sitter-chunking) needs Tree-sitter `Parser`/
  `Language` objects for TypeScript, TSX, JavaScript, and Python.
- **Decision:** Load grammars via `tree_sitter_language_pack.get_parser`/
  `get_language`, wrapped in `parser/grammars.py` behind `functools.cache`
  (the pack's own wrapper does not itself guarantee memoized construction).
  One dependency covers every grammar needed, with no manual
  `Language(...)` wiring per language.
- **Alternatives considered:** individual per-language packages
  (`tree-sitter-typescript`, `tree-sitter-javascript`, `tree-sitter-python`,
  ...) — more explicit pinning per grammar, but N extra dependencies for N
  languages. Rejected: no benefit at this scale, and
  `tree-sitter-language-pack` was already the declared dependency from the
  Day 01 scaffold.
- **Consequences:**
  - `.tsx` files must be routed to the pack's `"tsx"` grammar name, not
    `"typescript"` — `ingestion.languages` maps both `.ts`/`.tsx` to the
    single string `"typescript"`, and parsing JSX with the plain
    TypeScript grammar produces a broken parse. `resolve_grammar_name`
    takes an optional `path` hint purely for this routing; the public
    `ParseResult.language`/`Chunk.language` stay `"typescript"` either way.

---

## D-011 · Hand-rolled recursive AST walk, not Query/QueryCursor or `process()`

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** `tree-sitter-language-pack` 1.14.3 offers three ways to
  extract structure: raw `Query`/`QueryCursor` (this version's split API —
  `Query(language, src)` then `QueryCursor(query).captures(node)`, not the
  older `Language.query()`/`Query.captures()` shape), a built-in
  `get_tags_query()` per language, and a high-level `process()` that
  returns a pre-walked structure tree.
- **Decision:** Hand-roll a recursive `Node` walk per language family
  (`parser/extractor.py`'s `_dispatch_ts_js`/`_dispatch_python`) instead.
- **Consequences:**
  - The hardest requirement — qualifying a nested method as
    `f"{class_name}.{method_name}"` — needs explicit "what class am I
    currently inside" context threaded through the walk. Query captures
    return flat, parent-context-free node lists; a query-based approach
    would still need a manual `.parent` walk to recover that context, so
    Query buys nothing on the one thing that's hard.
  - `process()`/`get_tags_query()` have their own unverified conventions
    for anonymous-export naming and qualification; a hand-rolled walk
    gives full, directly testable control over which node types continue
    recursion vs. stop (keeping `.map(callback)`-style inline arrow
    functions and nested helpers out of the top-level symbol list) and
    over the enclosing-class-name tracking.
  - Costs more code than a one-line query per language; acceptable given
    the precision this pipeline's citation requirements demand.

---

## D-012 · `DEFAULT_MAX_CHUNK_LINES = 100` fallback-splitting threshold

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** `chunker/fallback.py` must split an oversized symbol along
  in-span line boundaries once it exceeds a configurable line budget.
- **Decision:** Default to 100 lines, defined locally in `fallback.py`
  (mirroring `ingestion/filters.py`'s `DEFAULT_MAX_FILE_SIZE_BYTES`
  placement — a module-local `Final` constant, not `config.py`).
- **Consequences:** The stack's default local embedding model
  (`all-MiniLM-L6-v2`, per CLAUDE.md) has a small context window
  (~256–384 tokens for MiniLM-class models). ~100 lines of code is
  roughly 500–800 tokens — generous enough that ordinary functions/methods
  are never split, but small enough to keep a genuinely oversized function
  from silently truncating (and losing citation/retrieval quality) at
  embedding time in Day 04. Threaded through `chunk_file(...,
  max_chunk_lines=...)` as an overridable keyword default.

---

## D-013 · Qualified `ClassName.method` naming for nested symbols

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Two different classes can define a same-named method (e.g.
  both have `render()`); a bare method name would collide across classes
  in the symbol/chunk index and in a future `find_symbol` MCP tool.
- **Decision:** `ParsedSymbol.name`/`Chunk.symbol` for a class-nested
  method is always `f"{class_name}.{method_name}"`, never a bare name.
  Anonymous classes/functions/default exports get a literal `"default"`
  name rather than being silently dropped.
- **Consequences:** Citations for methods are self-describing without
  needing the enclosing chunk's file path for disambiguation. `find_symbol`
  (Day 10) can match on either the qualified name or a suffix.

---

## D-014 · Pin `tree-sitter` to `<0.26` — 0.26.0 has a memory-corruption regression

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** While running Day 03's manual smoke test against a real
  TypeScript file (`p-queue`'s `source/index.ts`, 1001 lines), extracted
  symbols reported wildly incorrect `end_line` values (up to 27921 in a
  1001-line file) and the Python process segfaulted. Bisection (see
  below) isolated this to the `tree-sitter` core package itself, version
  0.26.0 — not `tree-sitter-language-pack`, not this project's extraction
  logic, and not something specific to Unicode/emoji content (reproduced
  on an ASCII-only copy of the same file). A purely synthetic file with
  the same shape (one class, 85 trivial methods) did **not** reproduce it,
  so this is triggered by some structural complexity in real-world
  TypeScript (deep generics, private `#field`s, decorators, etc.)
  combined with a longer walk, not raw node count alone.
- **Decision:** Cap the dependency at `tree-sitter>=0.23,<0.26` (was
  `>=0.23` with no ceiling). Verified via a throwaway virtualenv,
  bisecting exact version pairs against the same real-world fixture file:
  - `tree-sitter==0.26.0` + `tree-sitter-language-pack==1.14.3` → corrupted, segfaults.
  - `tree-sitter==0.26.0` + `tree-sitter-language-pack==1.0.0` → still corrupted (different garbage value) — confirms the regression is in `tree-sitter` core, not the language pack.
  - `tree-sitter==0.25.2` + `tree-sitter-language-pack==1.14.3` (latest pack) → correct, no crash.
  - `tree-sitter==0.24.0` + `tree-sitter-language-pack==1.14.3` → correct, no crash.

  The installed venv was reinstalled to `tree-sitter==0.25.2` (newest
  version confirmed clean, paired with the already-installed
  `tree-sitter-language-pack==1.14.3`).
- **Consequences:**
  - Re-running the same smoke test after the downgrade produces correct,
    in-file line ranges and no crash across all four `p-queue` source
    files tested.
  - Revisit this ceiling once a `tree-sitter` release past 0.26.0 exists
    and can be verified clean against this same fixture file (or once the
    upstream issue is identified/fixed upstream — this was not filed
    upstream as part of this session, worth doing before lifting the cap).
