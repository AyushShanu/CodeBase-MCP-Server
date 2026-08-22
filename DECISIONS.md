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

---

## D-015 · Hand-rolled `faiss-cpu` `IndexFlatIP`, not `langchain_community.vectorstores.FAISS`

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Day 04 (embeddings-vector-index) needs a persistent dense
  vector index over `Chunk` embeddings. CLAUDE.md's stack table lists FAISS
  as the primary vector store, but two implementation paths exist: the
  `langchain_community.vectorstores.FAISS` wrapper (paired with
  `HuggingFaceEmbeddings`, the "reference snippet" shape this stack was
  originally sketched around), or hand-rolling directly against
  `faiss-cpu`.
- **Decision:** Hand-roll a thin layer (`indexing/vector.py`) directly
  against `faiss.IndexFlatIP`, plus a custom chunk-metadata store, instead
  of using `langchain_community.vectorstores.FAISS`.
- **Alternatives considered:** `langchain_community.vectorstores.FAISS` —
  rejected. Its docstore is keyed on LangChain `Document` objects
  (`page_content`/`metadata: dict`), not this project's typed `Chunk`
  Pydantic model; round-tripping every `Chunk` field losslessly through
  that shape means either fighting the wrapper's assumptions or converting
  back and forth at every boundary. It also manages normalization somewhat
  opaquely depending on which distance strategy is configured, which
  conflicts with wanting explicit, directly-tested control over
  normalization (see D-016). A hand-rolled layer keeps `Chunk` the single
  source of truth end to end and is consistent with D-011's own precedent
  (Day 03's hand-rolled AST walk) of hand-rolling where precise control
  matters more than reuse. `langchain-community` is deliberately **not**
  added as a dependency for this.
- **Consequences:**
  - `indexing/vector.py` owns the full build/persist/load/query lifecycle
    itself; no LangChain vectorstore abstraction sits between `Chunk` and
    FAISS.
  - Persistence is two plain files under `INDEX_DIR`: `vector.faiss`
    (`faiss.write_index`/`read_index`) and `vector_metadata.json` (a JSON
    array of `Chunk.model_dump(mode="json")`, positional index = FAISS
    vector ID — no separate ID-mapping data structure needed).

---

## D-016 · `IndexFlatIP` over L2-normalized vectors for cosine similarity

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Correct semantic search needs cosine similarity, but FAISS's
  `IndexFlatIP` computes raw inner product, not cosine similarity, unless
  every vector involved is unit-length.
- **Decision:** Use `faiss.IndexFlatIP` (flat, brute-force — no
  `IndexIVFFlat`/quantization needed at this corpus scale, tens of
  thousands of chunks is trivial for CPU brute-force search) and enforce
  L2-normalization in exactly one function (`indexing.vector._l2_normalize`),
  applied to every vector added to the index **and** every query vector at
  search time, via a single shared code path (`embed_texts`) so build-side
  and query-side normalization can never drift apart.
- **Alternatives considered:** `IndexFlatL2` (Euclidean distance) — would
  require a separate distance-to-similarity conversion and doesn't map as
  directly onto "most relevant chunk" ranking. `IndexIVFFlat`/quantized
  indexes — unnecessary complexity at this scale; deferred until corpus
  size actually demands it.
- **Consequences:**
  - A near-zero-norm vector (degenerate/empty embedding, which shouldn't
    occur since empty-content chunks are filtered before embedding — see
    D-017) is left as an all-zero row rather than dividing by ~0.
  - Tested directly: `tests/test_indexing_vector.py` asserts every embedded
    vector's L2 norm ≈ 1.0, and that querying with content identical to an
    indexed chunk scores ≈ 1.0 (which only holds if both sides normalize
    identically).

---

## D-017 · Single batched `embed_documents` call per `embed_texts` invocation; batch size 32

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Embedding calls must never happen one-at-a-time per chunk
  (prohibitively slow at "tens of thousands of chunks" scale on a laptop
  CPU). Reading the installed `langchain_huggingface.HuggingFaceEmbeddings`
  source directly showed `embed_documents(texts)` already forwards the full
  list to `sentence_transformers.SentenceTransformer.encode(texts,
  **encode_kwargs)` in one call, and `encode()` itself batches internally
  via its own `batch_size` kwarg.
- **Decision:** `indexing.vector.embed_texts` calls `embedder.embed_documents(texts)`
  **once** with the complete text list, passing `batch_size` through
  `encode_kwargs`, rather than manually chunking `texts` into slices and
  issuing one `embed_documents` call per slice. Default
  `EMBEDDING_BATCH_SIZE = 32` (config-driven, `.env`-overridable) — matches
  `sentence-transformers`' own conventional default, a safe starting point
  for short code-chunk texts on CPU.
- **Alternatives considered:** manually chunking into `batch_size`-sized
  slices with one `embed_documents` call per slice — rejected as redundant
  complexity re-implementing batching `sentence-transformers` already does
  internally (CLAUDE.md: no abstractions beyond what's needed); it would
  also lose `sentence-transformers`' own internal length-based batching
  optimizations across the whole input at once.
- **Consequences:**
  - `HuggingFaceEmbeddings` is constructed fresh per `embed_texts` call
    (not cached via `functools.cache`, unlike `parser/grammars.py`'s
    parser/language caching) specifically so tests can
    `monkeypatch.setattr(vector, "HuggingFaceEmbeddings", FakeEmbeddings)`
    per-test without a stale cached instance leaking across tests (no
    `conftest.py`/fixture exists to clear an `lru_cache` between tests). A
    module-level singleton is a reasonable optimization once Day 08 gives
    the MCP server a real process lifecycle to hang it off — deliberately
    deferred, not forgotten.

---

## D-018 · `collect_repo_chunks` also catches `UnsupportedLanguageError`/`ParseError`, not just file-read `OSError`

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** `ingestion.languages.EXTENSION_LANGUAGE_MAP` marks ~25
  languages `included=True` (markdown, json, yaml, go, rust, java, ...),
  but `parser.grammars.LANGUAGE_TO_GRAMMAR` only configures
  `{typescript, javascript, python}`. `parser.extractor.parse_file` raises
  `UnsupportedLanguageError` for anything else *by design* (per its own
  docstring: "that gap must stay visible to the caller, not silently
  swallowed"). Discovered while implementing Day 04's repo-wide
  orchestration loop (`indexing.repo.collect_repo_chunks`): virtually any
  real repository (which will have at least a README.md) hits this, so an
  orchestrator that only guards the file-*read* step would crash on nearly
  every real repo.
- **Decision:** `collect_repo_chunks` wraps the `parse_file` call in
  `try/except (UnsupportedLanguageError, ParseError)`, in addition to
  wrapping the file read in `try/except OSError` — both failure modes
  append a `FileReadFailure(path, reason)` and `continue` the loop rather
  than aborting the whole-repo run. `chunk_file` itself is never wrapped
  (confirmed by reading `chunker/chunker.py` — it decodes with
  `errors="replace"` and never raises).
- **Consequences:**
  - A repo-wide chunk-collection run degrades gracefully on any file whose
    language has no Tree-sitter grammar configured yet (markdown, json,
    yaml, go, rust, ...) instead of crashing; the omission is recorded and
    visible via `RepoChunkCollection.read_failures`, not silent.
  - When Day 03's language coverage expands (Python was already optional;
    further languages may be added later), fewer files will hit this path
    — no code change needed here, the try/except degrades gracefully
    either way.

---

## D-019 · Bump `[tool.mypy] python_version` to 3.12 (type-checking target only, not runtime support)

- **Date:** 2026-08-21
- **Status:** Accepted
- **Context:** Day 04 adds a direct `numpy` dependency (`indexing/vector.py`
  needs `np.ndarray` for embedding vectors). numpy's own bundled `.pyi`
  stubs use PEP 695 `type` statement syntax, which mypy refuses to parse in
  *any* stub file when `python_version` is set below `"3.12"` — this
  applies to the stub's syntax, not our own code. mypy does not support a
  per-module `python_version` override (confirmed: it rejects
  `python_version` as a per-module flag), so a global bump was the only
  option; `follow_imports = "skip"` on a numpy-only override was tried
  first and did not avoid the parse error.
- **Decision:** Bump `[tool.mypy] python_version` from `"3.11"` to
  `"3.12"`. This governs only what syntax mypy accepts while
  type-checking — it does **not** change this project's actual runtime
  Python support, which remains `>=3.11` per `requires-python` and D-002.
  Our own source code introduces no 3.12-only syntax as part of this
  change.
- **Alternatives considered:** `follow_imports = "skip"` for
  `numpy`/`numpy.*` — tried, did not prevent mypy from parsing numpy's
  `__init__.pyi` and hitting the same syntax error. Leaving `numpy` out of
  static typing entirely (e.g. `# type: ignore` on the import) — rejected
  as a workaround that would silently disable type-checking for every
  numpy-typed value in `indexing/vector.py`, not just the stub-parsing
  issue.
- **Consequences:** mypy now accepts any 3.12-only syntax in our own code
  too, without complaint — a minor relaxation of the guard D-002 originally
  wanted, accepted as a reasonable trade-off to unblock a real, necessary
  dependency. Revisit if this ever causes 3.12-only syntax to land
  unintentionally in code meant to run on 3.11.
