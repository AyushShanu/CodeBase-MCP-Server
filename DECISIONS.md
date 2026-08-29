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

---

## D-020 · Hybrid retrieval: RRF merge, BM25 tokenization, pickle persistence, single-orchestration build (day-05-hybrid-retrieval)

- **Date:** 2026-08-22
- **Status:** Accepted
- **Context:** D-004 accepted a hybrid dense+sparse design but explicitly
  deferred the merge strategy to "when implementing `retrieval/`" — Day 05
  wires `rank-bm25` into `indexing/bm25.py` (mirroring `indexing/vector.py`'s
  build/persist/load/query shape) and merges its candidates with
  `indexing/vector.py`'s FAISS candidates in the new `retrieval/hybrid.py`.
  Several open questions had to be resolved to do this.
- **Decision — merge strategy: Reciprocal Rank Fusion (RRF), `k = 60`,
  1-indexed rank.** `score = sum(1 / (k + rank))` over whichever of
  {BM25 results, vector results} a chunk appears in; `rank` is 1-indexed
  (`enumerate(results, start=1)` — the top result in a list has `rank = 1`,
  never `0`). Implemented in `retrieval/hybrid._reciprocal_rank_fusion`.
  **Alternatives considered:** weighted-sum of normalized scores
  (`score = α·vector_score + (1-α)·bm25_score`) — rejected because BM25
  scores are unbounded/corpus-dependent while vector scores are bounded
  cosine similarities in `[-1, 1]`; combining them this way requires
  normalizing two incomparable scales and re-tuning `α` per corpus, whereas
  RRF only needs each list's rank order, already well-defined on both
  sides.
- **Decision — BM25 tokenization: lowercase, split on
  `[^a-zA-Z0-9]+`, no camelCase/snake_case sub-splitting.** Enforced in
  exactly one function (`indexing.bm25.tokenize`), reached identically at
  build time and query time — the same discipline D-016 applies to
  `_l2_normalize`. **Named limitation:** `generateToken` stays one token
  (camelCase is alnum-contiguous, nothing to split on), but
  `generate_token` *does* split into `["generate", "token"]` (`_` is itself
  non-alphanumeric) — so a query like `"generate token"` will not lexically
  match `generateToken` via BM25 (it remains reachable via the vector side).
  CamelCase/snake_case-aware sub-word splitting is a reasonable future
  improvement (Day 09/11 polish), not silently out of scope.
- **Decision — BM25 persistence: pickle for the BM25 state, JSON for chunk
  metadata.** `BM25Okapi` has no native serialization format, so
  `indexing.bm25.build_index` pickles `{"tokenized_corpus": ...,
  "bm25": BM25Okapi(...)}` to `bm25.pkl`, keeping chunk metadata in a
  separate `bm25_metadata.json` sidecar — mirrors `indexing/vector.py`'s
  FAISS-binary/JSON-metadata split (D-015) and narrows the pickle's blast
  radius to exactly what has no JSON-native serialization. **Only ever
  unpickle a file this process itself wrote to `index_dir`; never unpickle
  index data from an untrusted or externally-supplied path** — pickle
  deserialization of arbitrary input is a known code-execution risk (same
  untrusted-input discipline D-009 applied to cloned repo content).
- **Decision — `indexing.repo.build_all_indexes` as the single
  chunk-collection entry point.** Calls `collect_repo_chunks` exactly once,
  then builds both the vector and BM25 indexes from that one `list[Chunk]`
  — structurally enforces "same chunk set, same `Chunk.id` values on both
  indexes" rather than relying on convention; there is no code path where
  the two index builds can independently observe a repo that changed
  between them.
- **Decision — vector-side `score > 0.0` floor applied inside
  `retrieval/hybrid.py`, not `indexing/vector.py`.** The spec's own
  Definition of Done has two claims in tension: an empty/nonsensical query
  "may return a low-relevance result," while a query with no real match
  anywhere must return an *explicitly empty* list. `VectorIndex.query`
  (Day 04, unchanged) never thresholds by design — it always returns up to
  `top_k` neighbours regardless of relevance. `hybrid_search` filters
  vector candidates to `score > 0.0` before RRF, mirroring the floor
  `Bm25Index.query` already applies internally (`score <= 0` excluded) —
  this satisfies both DoD claims without touching Day 04's established
  `VectorIndex` contract.
- **Observation — BM25 query latency:** `rank_bm25.BM25Okapi.get_scores` is
  a linear scan over the whole corpus with no inverted index. Measured
  against a real clone of `sindresorhus/p-queue` (78 indexed chunks, 1215
  vocabulary terms): a single `Bm25Index.query(...)` call took ~0.045 ms —
  effectively instant at this scale. Revisit only if a much larger target
  corpus (many tens of thousands of chunks) makes this measurably show up
  in Day 07's per-question latency.
- **Observation — real-repo smoke test quality:** both an exact-symbol
  query (`"PQueue"`) and a natural-language query (`"where do we limit
  concurrent operations"`) against the real `p-queue` clone returned
  genuinely real code with correct file/symbol/line citations in the
  merged top-5. Ranking quality at this stage is rougher than ideal — BM25
  favors whichever chunk mentions the query term most densely (e.g. a test
  file over the actual class definition), and the vector side's generic
  `all-MiniLM-L6-v2` embeddings aren't code-specialized — but this is
  expected and acceptable for Day 05: Day 06 (reranking) is explicitly
  where retrieval quality gets refined further, and this day's job was
  correct hybrid merging with transparent scoring, not final ranking
  quality.
- **Consequences:** `retrieval.hybrid_search` raises `NoIndexAvailableError`
  only when *neither* index is built/loadable under `index_dir`; a query
  against two genuinely built indexes that matches nothing returns `[]`
  rather than raising, so Day 07's "not enough evidence" fallback can tell
  the two cases apart. BM25-specific exceptions (`Bm25NotBuiltError`,
  `Bm25LoadError`, `EmptyBm25IndexError`) are distinct from the
  vector-flavored ones already in `indexing/exceptions.py`, since
  `indexing/` is now the one stage running two index technologies
  concurrently and reusing the vector names would make
  `except IndexLoadError` ambiguous about which subsystem failed.

---

## D-021 · Cross-encoder reranking: model choice, score-scale non-combination, explicit max_length, hybrid_search wide-top_k calling contract (day-06-reranker)

- **Date:** 2026-08-25
- **Status:** Accepted
- **Context:** Day 05 gave the pipeline `retrieval.hybrid.hybrid_search`,
  whose RRF fusion score is a rank-order heuristic that never looks at the
  query and a chunk together (D-020's own "real-repo smoke test quality"
  observation named Day 06 as where retrieval quality gets refined
  further). CLAUDE.md's Day 06 line calls for a cross-encoder to rerank a
  *larger* hybrid candidate pool down to the strongest context. Several
  decisions had to be made and logged.
- **Decision — model: `cross-encoder/ms-marco-MiniLM-L-6-v2`.** CLAUDE.md's
  own "start here" default for the Reranker stack-table row; small
  (~80MB), well-established MS MARCO-trained, fast CPU inference for the
  scoring pass itself (see the measured latency below). **Alternatives
  considered (documented, not built):** `bge-reranker-base` (larger,
  generally stronger multilingual relevance, higher latency);
  `mxbai-rerank-base-v1` (newer, competitive quality, less battle-tested at
  this project's scale). Both swappable later purely via
  `RERANKER_MODEL_NAME` — no code change, since `CrossEncoder(model_name)`
  is the only place the name is consumed.
- **Decision — no new dependency.** `sentence-transformers>=3.0` (declared
  in `pyproject.toml` at Day 01 scaffolding, already used transitively via
  `langchain-huggingface`) already provides
  `sentence_transformers.CrossEncoder` — no `pyproject.toml` change.
- **Decision — score scale: `rerank_score` and `HybridQueryResult.score`
  (RRF) are never combined, averaged, or renormalized.** `CrossEncoder
  .predict()` returns a raw, unbounded relevance logit, not comparable to
  RRF's `1 / (k + rank)` fusion score. `rerank_score` alone determines
  reranked order; both scores stay visible on `RerankedResult` for
  transparency. Mirrors D-020's own RRF-over-weighted-sum reasoning (avoid
  combining two incomparable scales), applied one stage further
  downstream. Implemented in `reranker.rerank.rerank`; enforced by never
  introducing a combined field on `reranker.models.RerankedResult`.
- **Decision — explicit `RERANKER_MAX_LENGTH=512`, passed directly to
  `CrossEncoder(model_name, max_length=...)`.** `ms-marco-MiniLM-L-6-v2`
  defaults to a 512-token combined `(query, passage)` sequence length; a
  large chunk (a big function/class body from Day 03's chunking) can
  exceed that. Making the truncation point an explicit, stated value
  rather than an implicit library default that could silently change on a
  version bump. **Accepted limitation:** long chunks are truncated from the
  tail with no chunk-splitting/summarization workaround — code chunks
  typically carry their identifying signature near the top, so this is a
  reasonable tradeoff for this day, the same discipline D-020 applied to
  BM25's camelCase/snake_case tokenization limitation.
- **Decision — no cross-call caching.** `rerank` loads a fresh
  `CrossEncoder` instance per call, the same convention
  `indexing.vector.embed_texts` and `retrieval.hybrid.hybrid_search`
  already use. Day 08's MCP server owns model/index lifecycle once it
  exists; this day does not introduce caching the rest of the codebase
  doesn't have yet. **This is not free — see the measured construction
  latency below, which makes caching a clear priority for Day 08.**
- **Decision — calling contract with `hybrid_search`: any caller intending
  to rerank MUST call `hybrid_search(query,
  top_k=HYBRID_CANDIDATE_POOL_SIZE, ...)`, never rely on `hybrid_search`'s
  own default `top_k=10`.** `hybrid_search`'s default truncates the merged
  RRF list *before* reranking ever sees it, defeating the entire purpose of
  this stage ("reranks a *larger* hybrid candidate pool" — CLAUDE.md).
  Exercised directly in
  `tests/test_reranker.py::test_hybrid_search_wide_top_k_then_rerank_surfaces_candidate_default_top_k_would_cut_off`
  and visible in `FLOW.md` Section 3's sequence diagram. Must be repeated
  in Day 08's spec when `search_code` wires the two functions together.
- **Observation — reranker latency against the real `p-queue` clone (78
  indexed chunks, same clone D-020 measured):** `CrossEncoder(
  "cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)` construction
  took **~9.7-9.9 s**, measured consistently across repeated calls in both
  a fresh process and the same process (not a one-time download cost —
  every single call pays this). A single batched `.predict()` call over a
  `HYBRID_CANDIDATE_POOL_SIZE=50`-candidate pool took **~304 ms**.
  Construction cost is roughly **30x** the batched inference cost — the
  clear latency bottleneck of this stage, and strong evidence that Day
  08's MCP server should cache the `CrossEncoder` instance across queries
  rather than reconstruct it per call (the same way it will need to own
  index lifecycle for `indexing.vector`/`indexing.bm25`). Revisit this
  day's "no caching" decision at that point.
- **Observation — retrieval quality improvement:** for the semantic query
  `"how can I wait for the queue to become idle"`, the top hybrid-only
  (RRF, default `top_k`) result was `PQueue.#tryToStartAnother`
  (source/index.ts:280-329, an internal scheduling method unrelated to
  waiting/idling); after reranking a `top_k=HYBRID_CANDIDATE_POOL_SIZE`
  hybrid pool, the top result became the chunk at source/index.ts:616-715
  (covering `pause()`/`onIdle()`), a genuinely more relevant match for the
  question. For the exact-symbol query `"lowerBound"`, both hybrid-only and
  reranked top results agreed on the correct `lowerBound`
  (source/lower-bound.ts:3-20) chunk, confirming reranking does not
  regress an already-correct exact-match case.
- **Consequences:** `reranker.rerank.rerank` never re-embeds, re-tokenizes,
  or re-fetches chunk content — it consumes `HybridQueryResult.chunk
  .content` directly. `RerankedResult` (frozen Pydantic,
  `reranker/models.py`) nests the full `HybridQueryResult` under
  `hybrid_result` rather than flattening it, mirroring `HybridQueryResult`'s
  own nesting of `Chunk`. `benchmarks/questions.json` (14 questions —
  5 `exact_symbol`, 5 `semantic`, 4 `structural`) is committed as a tracked
  source file against the real `p-queue` clone and doubles as Day 11's V2
  eval set.

## D-022 · LLM generation & citations: five-provider fallback chain, structured-JSON robustness, deterministic anti-fabrication (day-07-llm-generation-citations)

- **Date:** 2026-08-26
- **Status:** Accepted
- **Context:** Day 06 left the pipeline at `reranker.rerank.rerank` —
  quality-ordered evidence, not an answer. CLAUDE.md's Day 07 line calls
  for "RAG generation through the NVIDIA NIM → Groq → OpenRouter fallback
  chain," a strict evidence-only prompt, and a "not enough evidence"
  fallback. Several decisions had to be made and logged.
- **Decision — wire real adapters for all five candidate providers (NVIDIA,
  Groq, OpenRouter, Gemini, Local), not one-plus-documented-alternatives.**
  This diverges from Day 04/06's pattern (pick one model, document the rest
  as swappable but unbuilt) because CLAUDE.md's requirement here is the
  fallback chain's *runtime* behavior itself — "never a single hardcoded
  provider" — not a single chosen model. A chain that only exists on paper
  for four of five providers wouldn't satisfy that requirement.
- **Decision — confirmed free-tier default model per provider** (checked
  against each provider's live catalog/docs during implementation on
  2026-08-26, not assumed from training data — all overridable via `.env`):
  `NVIDIA_MODEL_NAME=nvidia/llama-3.3-nemotron-super-49b-v1` (NVIDIA's
  older small default, `meta/llama-3.1-8b-instruct`, was deprecated
  2026-08-25 — the day before this was checked — so it is deliberately not
  used); `GROQ_MODEL_NAME=llama-3.1-8b-instant` (confirmed current,
  free-tier); `OPENROUTER_MODEL_NAME=meta-llama/llama-3.3-70b-instruct:free`
  (OpenRouter's free-model catalog rotates weekly by design; this slug was
  reported as the longest-running/most-established free model as of
  2026-08, not a permanent guarantee); `GEMINI_MODEL_NAME=gemini-2.5-flash`
  (current stable free-tier model; `gemini-2.0-flash` was deprecated
  mid-2026). `LOCAL_MODEL_NAME` has no default — the operator supplies
  their own (e.g. CLAUDE.md's optional Qwen2.5-Coder-7B-Instruct Q4_K_M).
- **Decision — config-time precedence and runtime fallback are two
  distinct mechanisms, never conflated.** `providers.registry
  .select_providers` decides *which providers are even candidates* (NVIDIA
  → Groq → OpenRouter → Gemini → Local order, only those with a credential
  set), purely from `.env` — it has no awareness of whether a call will
  succeed. `pipeline.generate_answer` decides *runtime fallback* — it
  iterates that ordered list and moves to the next candidate on a
  `ProviderRequestError` (including a timeout) or an exhausted JSON-retry
  budget. `NoProviderConfiguredError` (raised by `select_providers` when
  the list would be empty) and `AllProvidersFailedError` (raised by
  `generate_answer` once every candidate has actually failed) are kept as
  distinct exception types precisely so callers/tests can tell "nothing
  was ever configured" apart from "candidates existed and all failed" —
  directly tested in `tests/test_generation.py`.
- **Decision — no new dependency.** `httpx>=0.27` (declared in
  `pyproject.toml` since Day 01, unused anywhere until this day) is
  sufficient for all five providers' plain HTTPS/JSON REST endpoints — no
  `openai`, `google-generativeai`, or other provider SDK was added. The
  four OpenAI-chat-completions-shaped backends (NVIDIA, Groq, OpenRouter,
  Local) share one `_openai_compatible.py` implementation, parametrized by
  `base_url`/`api_key`/`model_name` — four call sites with an identical
  request/response shape is exactly the "collapse three similar lines"
  case, not premature abstraction. Gemini's `generateContent` REST shape
  differs enough (auth via a `key=` query parameter, a different
  `contents`/`systemInstruction` envelope) that it gets its own separate
  adapter instead of being forced into the shared one.
- **Decision — structured-JSON output via a dedicated extraction step,
  not raw validation.** Free-tier instruct models routinely wrap valid
  JSON in ` ```json ... ``` ` fences or add a sentence of prose despite
  explicit instructions not to — a well-known, common failure mode.
  `pipeline._extract_json_object` strips a leading/trailing code fence and
  then brace-matches forward from the first `{`, tracking whether the scan
  is inside a JSON string literal (respecting `\"` escapes) so a literal
  brace inside the `"answer"` string value doesn't break the match — this
  correctness detail is directly unit-tested
  (`test_extract_json_object_handles_braces_inside_string_values`). This
  runs on every provider's raw text *before*
  `generation.models._LLMStructuredOutput.model_validate_json(...)` is
  attempted, and never raises itself — an unparseable response fails
  naturally at schema validation, counting as one JSON-retry attempt. As
  defense-in-depth on top of this (not a substitute for it), the four
  OpenAI-compatible adapters request `response_format: {"type":
  "json_object"}` and the Gemini adapter requests `generationConfig
  .responseMimeType: "application/json"` — neither is honored by every
  free-tier model/provider combination, hence still needing the extraction
  step.
- **Decision — `GENERATION_JSON_RETRY_LIMIT` (default `1`) means retries
  *after* the first attempt; total attempts per provider = limit + 1.**
  Chosen over the alternative reading ("limit = total attempts") because
  it makes `0` a coherent "no retries, one shot per provider" setting
  rather than a confusing "zero attempts." A request-level failure
  (`ProviderRequestError`) is never retried in place — only a
  JSON-validation failure gets the same-provider repair-prompt retry; a
  request failure moves straight to the next configured provider.
- **Decision — `GENERATION_REQUEST_TIMEOUT_SECONDS` (default `30`),
  passed explicitly to every `httpx` call.** Mirrors D-009's discipline for
  `git clone` (`RepoCloneTimeoutError` rather than hanging indefinitely) —
  Day 08 will call `generate_answer` synchronously from an MCP tool
  handler, so an unbounded provider call would become a hung tool call
  from Claude Code/Desktop's perspective. A timeout is caught and
  re-raised as `ProviderRequestError`, participating in the normal
  fallback-to-next-provider path exactly like any other request failure —
  directly tested.
- **Decision — deterministic, anti-fabrication citation attachment is the
  actual "no fabricated citations" mechanism, not the prompt.** The model
  supplies only `cited_chunk_ids` (a list of strings); `citations.attach
  .attach_citations` is the *only* place a `Citation` is constructed, and
  every field on it is copied from the matching
  `candidate.hybrid_result.chunk` in the `candidates` this specific call
  was given — never from anything the model's `answer` text asserts. An
  ID absent from `candidates` (hallucinated or stale) is dropped silently
  (logged at `warning`), never fabricated into a citation. A citation list
  that comes back empty forces `GeneratedAnswer.has_sufficient_evidence`
  to `False` regardless of what the model's own JSON claimed — a model
  cannot assert sufficient evidence backed by zero real citations; this
  override is directly unit-tested
  (`test_generate_answer_forces_has_sufficient_evidence_false_when_cited_ids_are_all_unknown`).
- **Decision — `Citation` is a flat 5-field projection of `Chunk`
  (`chunk_id`, `file`, `symbol`, `start_line`, `end_line`), a deliberate
  exception to the nest-don't-flatten convention `HybridQueryResult`/
  `RerankedResult` established.** Those two models nest the full upstream
  object because they exist for evidence *inspection* (comparing BM25 vs.
  vector vs. rerank signals). A `Citation` exists to be handed back to a
  tool caller/user as a small, final-answer-facing summary — nesting a
  full `Chunk` (including its full `content` text) would leak retrieval
  internals into what should be a minimal pointer. This is a conscious
  divergence, not an inconsistency.
- **Decision — named, only-partially-mitigated prompt-injection risk.**
  This is the first day untrusted, attacker-reachable content (arbitrary
  cloned-repo text — D-009's own "main untrusted-input boundary") is fed
  into a live LLM prompt and turned into free-text prose a user reads. The
  citation *mechanism* above is already well-protected — a malicious code
  comment cannot fabricate a fake file/line claim that survives
  `attach_citations`, since citations are never sourced from LLM text.
  What is **not** protected is the `answer` string itself, unconstrained
  model prose that could in principle be steered by adversarial text
  inside a retrieved chunk. Full mitigation (content sanitization,
  injection detection) is explicitly CLAUDE.md's own Day 11 scope
  ("prompt-injection-aware handling") and is not rebuilt here — this day
  adds only the one cheap, immediate mitigation available now:
  `generation.prompts.SYSTEM_PROMPT` explicitly instructs the model that
  evidence blocks are data to answer from, never instructions to follow,
  even if their text appears to contain directives or commands (directly
  string-asserted in
  `test_system_prompt_instructs_that_evidence_blocks_are_data_not_instructions`).
  Extended to Gemini's own auth scheme: since its API key lives in a `key=`
  URL query parameter rather than an `Authorization` header, `gemini.py`
  additionally never logs the full request URL verbatim on failure, only
  the model name and HTTP status — the same "never log secrets" discipline
  `base.py` states for the four header-authenticated providers, applied to
  Gemini's different auth mechanism.
- **Decision — `Provider` is a `typing.Protocol`, not an `abc.ABC`.** No
  interface/ABC precedent exists anywhere else in this codebase; structural
  typing lets `tests/test_generation.py`'s `_FakeProvider` satisfy the
  interface without importing or subclassing anything, consistent with
  this project's "don't build abstractions before they're needed" ethos.
- **Decision — `httpx.MockTransport` injected via an optional
  constructor-time `transport` parameter on `OpenAICompatibleProvider`/
  `GeminiProvider`, not by monkeypatching `httpx.Client` globally.** Keeps
  `Provider.complete(self, *, system, user)`'s signature exactly what the
  interface specifies (no test-only parameters on the call itself); the
  seam lives at construction time and defaults to `None` (a real client)
  in production. Directly exercised in `tests/test_generation.py`'s
  `_openai_compatible.py` request/timeout/parsing/error-wrapping/
  no-secret-logging tests.
- **Observation — manual smoke test against the real `p-queue` fixture
  repo (Days 03–06's fixture): not run in this implementation session** —
  no provider API key or local model server was available in this sandboxed
  environment (`.env` does not exist; no `NVIDIA_API_KEY`/`GROQ_API_KEY`/
  `OPENROUTER_API_KEY`/`GEMINI_API_KEY`/`LOCAL_MODEL_NAME` set). The full
  automated test suite (`tests/test_generation.py`, `tests/test_citations.py`,
  the `tests/test_config.py` additions) passes with zero real network calls
  (`httpx.MockTransport` only), and `ruff check`/`ruff format --check`/
  `mypy` all pass on the new code. **Pending:** the operator must run
  `hybrid_search(..., top_k=HYBRID_CANDIDATE_POOL_SIZE)` → `rerank(...)` →
  `generate_answer(...)` end-to-end against `benchmarks/questions.json`
  with a real configured provider (at minimum one free-tier key), record
  which provider actually served each call and its wall-clock time, and
  confirm one deliberately off-topic question returns
  `has_sufficient_evidence=False` honestly — then append those numbers
  here.
- **Consequences:** `generation/` and `citations/` are fully implemented
  and tested end-to-end at the unit level; Day 08's MCP tools call into
  `generation.pipeline.generate_answer` rather than adding new generation
  logic. **Flagged, not silently resolved:** CLAUDE.md's MCP Tool Contract
  table (`search_code`, `find_symbol`, `get_file_context`, `analyze_impact`,
  `repository_summary`) has no tool that obviously exposes a generated
  answer — `search_code`'s stated purpose is "returns ranked code
  evidence," not "returns an answer." Day 08's spec must explicitly decide
  whether `search_code` gains an answer-synthesis mode, a new tool
  (e.g. `ask`) is added, or generation stays client-side.

## D-023 · MCP server V1: SDK migration, ask tool, startup caching, manifest, stdout/stderr discipline, asyncio.Lock (day-08-mcp-server-v1)

- **Date:** 2026-08-27
- **Status:** Accepted
- **Context:** Days 02–07 built a fully independent, well-tested pipeline
  that nothing outside `tests/` had ever driven end-to-end. Day 08 turns it
  into a real MCP server. Four things made this larger than "wire four
  functions together," each requiring its own decision.
- **Decision — migrate off the low-level `mcp.server.Server` API onto
  `mcp.server.MCPServer`.** The Day 01 stub and `tests/test_mcp_server.py`
  used `Server`'s `@server.list_tools()`/`@server.call_tool()` decorators,
  which do not exist on the installed `mcp==2.0.0` (`pyproject.toml` only
  pinned `mcp>=1.0`) — confirmed directly: `AttributeError: 'Server' object
  has no attribute 'list_tools'`. This was a genuine, silent version drift:
  3 pre-existing `mypy`/`pytest` failures on `main` predate this spec.
  `MCPServer` (the SDK's `mcp.server.fastmcp` → `mcp.server.mcpserver`
  rename) is the current high-level API: `@server.tool()` derives both
  JSON input and output schema from a plain function's type hints,
  including full Pydantic support for both params and return type
  (verified directly: a function returning a Pydantic `BaseModel` produces
  a matching `structured_content` dict with no hand-written JSON Schema).
  Exception handling over the real stdio transport is automatic —
  `MCPServer._handle_call_tool` wraps any plain exception (not `MCPError`)
  into `CallToolResult(content=[TextContent(text=str(e))], is_error=True)`,
  confirmed by reading the wiring into `run()`'s dispatch loop, not just
  the `call_tool()` convenience method — so tool functions raise clear,
  typed exceptions and rely on the SDK for clean error surfacing, never
  hand-rolling try/except-and-format per tool.
- **Decision — tighten `pyproject.toml`'s `mcp>=1.0` to `mcp>=2.0,<3.0`.**
  Mirrors D-014's `tree-sitter` pin precedent (pin after finding a real
  regression) — a future `mcp` major bump now fails loudly at install time
  instead of silently reintroducing this same class of breakage.
- **Decision — add a fourth tool, `ask`, beyond CLAUDE.md's literal V1
  table.** Resolves D-022's own flagged gap (no V1 tool exposed a generated
  answer) using the name `FLOW.md`'s Section 3 heading had already
  anticipated. `ask` wires the full pipeline
  (`hybrid_search` → `rerank` → `generate_answer`) and returns
  `generation.models.GeneratedAnswer` directly — no wrapper model needed,
  it is already tool-response-shaped. CLAUDE.md's "MCP Tool Contract" table
  now has an `ask` row (V1) reflecting this, rather than staying silently
  out of sync with what was actually built.
- **Decision — startup-time caching via `MCPServer`'s `lifespan` async
  context manager, exactly fulfilling what D-020/D-021 explicitly
  deferred to "Day 08's MCP server."** The vector index, BM25 index, one
  `CrossEncoder`, and (see below) one embedding-model instance are each
  loaded/constructed exactly once per server connection and reused for
  every subsequent tool call — D-021 measured `CrossEncoder` construction
  at ~9.7-9.9s, far too slow to pay per call. Mechanism, confirmed by
  running real code against the installed SDK: a tool function declares a
  `ctx: Context[_ServerState, Any]` parameter and reads
  `ctx.request_context.lifespan_context` (**not** `ctx.lifespan`, a
  different, unpopulated attribute on a different `Context` class) — the
  same object (`id()`-identical) is threaded across every tool call in one
  connection's lifetime, verified directly. `retrieval.hybrid.hybrid_search`
  and `reranker.rerank.rerank` each gained purely additive optional
  parameters (`bm25_index`/`vector_index`/`embeddings` and `cross_encoder`
  respectively) — `None` (the default) preserves every prior day's exact
  behavior byte-for-byte; every pre-existing test in
  `tests/test_retrieval_hybrid.py`/`tests/test_reranker.py` passes
  unmodified. `mcp/server.py` is the first real caller passing its cached
  instances through.
- **Observation, discovered while testing this day's own caching goal —
  `VectorIndex.query`'s per-call embedding-model construction was itself
  uncached and undermined the day's stated purpose.** Caching the
  `VectorIndex`/`Bm25Index` *objects* (as originally scoped) left
  `VectorIndex.query`'s internal `embed_texts([text], ...)` call
  constructing a fresh `HuggingFaceEmbeddings` instance on *every single
  query* regardless — confirmed by a counting test (1 construction per
  `.query()` call, `0` extra from `load_index` itself) — and each
  construction was found to trigger real network calls to
  huggingface.co (an "is this model current" check), directly
  contradicting CLAUDE.md's "embedding models run locally by default"
  principle for the query path specifically. **Decision (scope extended
  after flagging to the user): give `indexing.vector.embed_texts` and
  `VectorIndex.query` the identical additive-optional-parameter treatment**
  (`embeddings: HuggingFaceEmbeddings | None = None`, threaded through
  `hybrid_search`'s new `embeddings` parameter) — `mcp/server.py`'s
  `_ServerState` now also holds one cached `embeddings` instance
  (constructed only when `vector_index is not None` — no point building it
  otherwise), verified to be constructed exactly once across multiple
  `search_code`/`ask` calls in the same manual smoke-test session as the
  `CrossEncoder`/index counts.
- **Decision — the per-index manifest (`indexing/manifest.py`,
  `IndexManifest(repo_root, source)`) is the one piece of state
  `get_file_context` needs.** `Chunk.file` is repo-relative by design;
  nothing before this day recorded the absolute checkout root anywhere
  persisted, so a `codebase-rag serve` process started fresh (a different
  process, possibly long after `codebase-rag index` ran) had no way to
  resolve a chunk's `file` back to a real path. `indexing.repo
  .build_all_indexes` writes it immediately after both index builds
  succeed, reusing the function's existing `repo: str = ""` parameter as
  the manifest's `source` rather than adding a second, possibly-diverging
  field. `load_manifest` never raises — a missing manifest (an
  older/manifest-less index) degrades `get_file_context` to a clear
  per-call `RepoRootUnknownError`, not a server-startup failure, since the
  other three tools don't need it.
- **Decision — `get_file_context`'s scope is "any real file under
  `repo_root`," not "any file that was actually indexed" — a named,
  accepted V1 limitation.** CLAUDE.md's own Day 11 line names "secret-file
  exclusion" as planned, unbuilt V2 scope, meaning a checked-out repo can
  contain a file (e.g. a committed `.env`) that Day 02's ingestion filters
  correctly excluded from chunking but that is still physically present
  under `repo_root` and therefore readable through this tool today. Not
  rebuilt here — same deferral pattern D-022 used for prompt-injection
  hardening.
- **Decision — `get_file_context`'s path-containment check mirrors
  `ingestion.scanner.scan`'s exact resolve-then-`relative_to` discipline**
  (`Path.resolve(strict=True)` on the root, then `resolved.relative_to
  (root_real)` catching `ValueError` to detect an escape — including one
  only visible after resolving an intermediate symlink), applied fresh as
  a new, separate `get_file_context`-specific check rather than reusing
  `scanner.py`'s code path directly, since this is single-request file I/O
  at query time, not a bulk directory walk. Unlike `scan()`'s
  "exclude-and-continue," a single explicit request *raises*
  `PathOutsideRepoRootError` — tested with both a plain `../` traversal and
  a real symlink escaping `repo_root`. This is the one cheap containment
  check this day adds; full "path restriction" hardening stays Day 11
  scope (CLAUDE.md's own line), the same "one cheap mitigation now, full
  hardening later" pattern D-022 established for prompt injection.
- **Decision — `find_symbol`'s exact + qualified-suffix matching, with an
  explicitly non-retrieval `score`.** `chunk.symbol == symbol` is exact
  (`score=1.0`); `chunk.symbol.endswith(f".{symbol}")` matches a bare name
  against a qualified name's trailing component (e.g. `"pause"` matches
  `"PQueue.pause"`, per D-013's `ClassName.method` naming) at `score=0.5`.
  These scores are a match-quality indicator only — `find_symbol` is a
  deterministic lookup over cached `.chunks`, not a ranking model, unlike
  `search_code`'s `score` (a real cross-encoder logit). Definitions only:
  no usages/callers/references until Day 10's `analyze_impact` — stated in
  the tool's own description, not just this file, so the limitation is
  visible to whatever client calls it. Zero matches returns `matches: []`,
  never an error, mirroring `hybrid_search`/`rerank`'s own "empty is a
  valid result" convention.
- **Decision — stdout/stderr discipline: environment variables plus
  explicit `logging.basicConfig(stream=sys.stderr, ...)`, set at the very
  top of `mcp/server.py` before any pipeline import.** stdio's JSON-RPC
  stream lives on stdout; `sentence-transformers`/`transformers`/
  `huggingface_hub` print progress/verbosity noise directly to stdout on
  first model construction, bypassing `logging` entirely — confirmed by
  running the module and capturing stdout/stderr separately: with the
  discipline in place, a full startup (FAISS load, embedding + reranker
  model construction) produces zero stdout output, all log lines landing
  on stderr. Sets `HF_HUB_DISABLE_PROGRESS_BARS=1`, `TRANSFORMERS_VERBOSITY
  =error`, `TOKENIZERS_PARALLELISM=false` via `os.environ.setdefault(...)`
  (never overriding an operator's own explicit setting) before importing
  `indexing`/`reranker`/`generation`/`sentence_transformers`/
  `langchain_huggingface` anywhere in the module — this forces `# noqa:
  E402` on every import below that point, which is intentional and stated
  in the module's own docstring, not something to "fix" later.
- **Decision — a single `asyncio.Lock` in `_ServerState` guards
  `hybrid_search`/`rerank` (used by `search_code`/`ask`); `get_file_context`
  never acquires it (touches only the immutable `manifest` reference), and
  `ask`'s `generate_answer` call runs outside it (a network call, not
  shared local-model state, so serializing it against `search_code`/
  `find_symbol` would cost latency for no safety benefit).** **Honest
  finding, stated explicitly rather than left implicit:** `hybrid_search`
  and `rerank` are and must stay fully synchronous (D-020/D-021's own
  constraint) — a tool coroutine's `async with state.lock: <synchronous
  call>` has no `await` inside the critical section, so Python's
  single-threaded asyncio scheduler cannot switch tasks there regardless of
  whether the lock exists. No test routed through the real, synchronous
  pipeline functions can meaningfully falsify "the lock does nothing." The
  lock is still a deliberate, forward-looking invariant (declared correct
  as this codebase's helpers exist today; would start mattering the moment
  either helper ever gains a genuine `await`, e.g. an async model backend),
  not an unconsidered gap — tested by isolating the exact locking *pattern*
  `search_code`/`ask` use around a stand-in coroutine that has a genuine
  internal `await`, proving two `asyncio.gather`'d holders never interleave.
- **Decision — a minimal `codebase-rag index <source>` CLI subcommand,**
  the bare enabler needed to make the MCP server's own tools runnable
  end-to-end against a real repo (`ingestion.loader.load_repo` →
  `indexing.repo.build_all_indexes`, printing basic stats). Deliberately
  not CLI polish (flags, progress bars, incremental re-indexing) — that
  stays Day 11 scope, as CLAUDE.md already states.
- **Observation — real end-to-end smoke test** (a 3-chunk fixture repo,
  fake embeddings/CrossEncoder, real `MCPServer`/lifespan/dispatch via
  `mcp.client._memory.InMemoryTransport` + `ClientSession`): `search_code`,
  `find_symbol` (exact and qualified-suffix), `get_file_context` (including
  a rejected `../` traversal and a rejected symlink escape), and `ask`
  (honest `is_error` failure with a clear message when no provider is
  configured, the expected state for a fresh clone with no `.env`) all
  behaved as designed. A direct counting test confirmed `HuggingFaceEmbeddings`
  and `CrossEncoder` are each constructed exactly once across two
  `search_code` calls plus one `ask` call in the same connection.
- **Consequences:** `mcp/server.py`'s `ping` stub is fully removed, not
  kept alongside the real tools. `generation`/`citations` (Day 07) are
  consumed unchanged. Day 10's `analyze_impact`/`repository_summary` (V2)
  remain unbuilt; `find_symbol`'s "definitions only" and
  `get_file_context`'s "any real file under repo_root" limitations are
  the explicit seams Day 10/11 pick up.

---

## D-024 · Symbols, references & impact analysis: reference-scanner node types, `#partN`/whole-file-fallback `caller_symbol` convention, full-path-before-basename import resolution, `is_likely_test` heuristic, `MAX_IMPACT_REFERENCES_PER_KIND` cap, graceful LLM degradation (day-10-symbol-impact-analysis)

- **Date:** 2026-08-28
- **Status:** Accepted
- **Context:** `find_symbol` (Day 08) only ever looked up symbol
  *definitions* — its own docstring already named "reference tracking" as
  Day 10 scope. This day adds that missing layer and the `analyze_impact`
  tool CLAUDE.md's demo question 4 needs. Several sub-decisions below.
- **Decision — a second, full-tree recursive Tree-sitter walk per
  language family (`parser/extractor.py`), run against the same
  already-parsed tree, never a second `parser.parse()` call.** The
  existing symbol walk (`_dispatch_ts_js`/`_dispatch_python`) is
  deliberately stop-early (never recurses into function/method bodies,
  keeping nested helpers/callbacks out of the symbol list); the reference
  walk needs the opposite policy since calls happen inside those bodies.
  Node/field shapes were empirically verified (not assumed from memory)
  against this repo's installed `tree_sitter_language_pack` before
  writing extraction code:
  - Python `call`: callee under field `"function"`; bare = `identifier`;
    `obj.method()`/`a.b.c()` = `attribute` with its own `"attribute"`
    field already isolating the trailing name (no manual chain-walking).
  - Python `import_statement`: **no `module_name` field** — each named
    module is a `children_by_field_name("name")` child, either a
    `dotted_name` or an `aliased_import` (whose own `"name"` field holds
    the `dotted_name`). One `RawReference` **per distinct module**
    (`import os, sys` → two), since they are genuinely different modules.
  - Python `import_from_statement`: has `"module_name"` → `dotted_name`.
    One `RawReference` for the **whole statement** (`from a.b import c, d`
    → one reference, `module="a.b"`) since every imported name shares one
    source module and resolution is module-path-only, never
    per-imported-name. `from . import x`'s `module_name` field IS present
    but has type `relative_import` (decoded text `"."`) — decoded as-is
    rather than specially resolved; a `"."` module never matches anything
    meaningful during resolution, a deliberate, accepted V2 gap.
  - TS/JS/TSX `call_expression`: callee under field `"function"`; bare =
    `identifier`; `obj.method()`/`a.b.c()` = `member_expression` with its
    own `"property"` field. `new Widget()` is a **different** node type
    (`new_expression`) — deliberately not captured (spec names only
    `call_expression`).
  - TS/JS/TSX `import_statement`: field `"source"` → a `string` node;
    unquoted specifier read from its `string_fragment` named child
    (falling back to quote-stripping if absent). Same extraction function
    reused for `typescript`/`tsx`/`javascript` (mirrors `_EXTRACTORS`'s
    existing three-key mapping).
- **Decision — `indexing/references.py`'s `load_index` hybrid: absence
  of `references.json` returns `None` (never raises, mirrors
  `manifest.load_manifest`'s leniency); corruption of a file that DOES
  exist raises `ReferenceIndexLoadError` (mirrors `vector`/`bm25.load_index`'s
  strictness).** Deliberately not a pure copy of either existing pattern
  — an absent index is a normal state (older index, or zero recognized
  references), while a broken-but-present file is a real data-integrity
  problem that should never be silently swallowed into "pretend it
  doesn't exist." Confirmed with the user before implementation as a
  genuine design choice, not an obvious default.
- **Decision — `CallerInfo.caller_symbol` strips any `chunker.fallback
  .split_oversized_symbol`-applied `#partN` suffix (regex `r"#part\d+$"`,
  extracted into the new shared `impact/symbols.py`), and is `None`
  (never `""`) for a call site inside a whole-file-fallback chunk
  (`symbol == ""`, `type == SymbolKind.MODULE`) or when no containing
  chunk spans the reference line at all.** The raw `#partN` suffix is an
  internal chunk-storage artifact that must never leak into an
  externally-facing report; `None` represents "no enclosing symbol to
  name" as a real, distinct case rather than a misleading empty string.
  `file`/`line` on `CallerInfo`/`ImporterInfo` stay exact regardless —
  only the derived `caller_symbol` display value is cleaned up.
- **Decision — `_match_symbol`'s exact + qualified-suffix matching logic
  extracted once into `impact/symbols.py:match_symbol_chunks`, with
  `mcp/server.py:_match_symbol` reduced to a thin wrapper.** Both
  `find_symbol` and `analyze_impact` now call the same implementation —
  verified byte-for-byte identical `find_symbol` test output before and
  after the extraction (no behavior change, pure refactor). The same
  module also gained `count_distinct_definitions` (how many distinct
  `(file, unsuffixed-qualified-symbol)` definitions repo-wide share a
  bare trailing name) — the mechanism behind CONFIRMED (`<= 1`) vs LIKELY
  (`> 1`) caller-confidence labeling. Name-based matching, not full
  type/scope resolution, is an explicitly accepted V2 simplification
  (CLAUDE.md rules out "perfect whole-program static analysis"); this
  labeling exists precisely to keep that limitation visible rather than
  overstating precision.
- **Decision — import resolution (`impact/analyzer.py:_resolves_to`)
  tries a full repo-relative path candidate FIRST, falling back to a
  bare-basename comparison only when no concrete full-path candidate
  could be built at all (a bare token specifier, e.g. Python `import os`
  or an npm package name).** A concrete-but-non-matching full-path
  candidate is a **definitive rejection**, never a trigger for the
  basename fallback — verified against a real false positive this exact
  ordering prevents: two same-named files in different packages
  (`pkg_a/auth.py` vs `pkg_b/auth.py`) must not cross-match. A
  basename-only match still stays `LIKELY`, never promoted to
  `CONFIRMED`. **Bug found and fixed during this day's own manual
  end-to-end verification** (run against the real `p-queue` demo repo,
  not just unit fixtures): a relative specifier's extension (e.g.
  `import PQueue from "../source/index.js"`, TS/ESM's common
  compiled-`.js`-from-`.ts`-source convention) was not being stripped
  before comparison against the already-extension-stripped target file,
  so a real, correct import was silently missed. Fixed by extension-
  stripping every relative/slash-containing candidate in
  `_module_to_candidate_paths` before it's ever compared; added as a
  regression test (`test_analyze_impact_import_resolution_strips_js_extension_from_relative_specifier`)
  since no unit fixture had originally exercised an extensioned relative
  specifier.
- **Decision — `is_likely_test` (`impact/analyzer.py`) matches on path
  segments (`PurePosixPath(file).parts[:-1]`, case-insensitive
  `"test"`/`"tests"`) or a filename regex (`test_*.py` / `*_test.py` /
  `*.test.ts` / `*.spec.ts`), never a bare substring search.** This is
  what keeps `contest.py`/`latest_config.py` from being misflagged —
  verified both directions with dedicated tests, and confirmed correct
  against the real p-queue repo (every real caller under `test/` flagged
  `is_likely_test=True`).
- **Decision — `callers`/`importers` are each capped at
  `MAX_IMPACT_REFERENCES_PER_KIND = 50`** (a module-level `Final[int]` in
  `impact/analyzer.py`, same locally-scoped-constant convention
  `chunker/fallback.py`'s `DEFAULT_MAX_CHUNK_LINES` already set, not a
  new `config.py` entry), **with a `callers_truncated`/`importers_truncated`
  flag set whenever the real count exceeds the cap** — a very common bare
  name (`run`, `close`, `get`) must never return an unbounded list, and a
  capped list must never be presented as if it were exhaustive (the LLM
  narration step is explicitly told when truncation occurred and
  instructed not to imply completeness).
- **Decision — `analyze_impact` degrades gracefully to `explanation=None`
  when every configured LLM provider fails or none is configured, rather
  than failing the whole tool call.** `impact/analyzer.py` catches
  `NoProviderConfiguredError`/`AllProvidersFailedError` from
  `impact.explain.explain_impact` and returns the full `ImpactResult`
  with all deterministic evidence intact. This is a deliberate,
  user-confirmed divergence from `ask`'s existing hard-fail-on-no-provider
  behavior (D-023) — `ask` has nothing useful to return without an LLM;
  `analyze_impact`'s deterministic evidence (defs/callers/importers) is
  real, already-computed value that an LLM outage shouldn't discard.
  Observed live during manual verification against p-queue: the
  repo's real (but non-functional, 404-ing) `GROQ_API_KEY` caused
  `explain_impact` to genuinely exhaust its provider chain, and
  `analyze_impact` correctly returned full evidence with
  `explanation=None` rather than erroring the whole call.
- **Decision — `explain_impact` (`impact/explain.py`) duplicates
  `generation.pipeline`'s `_extract_json_object`/`_build_retry_prompt`
  helpers locally rather than importing them (they're private,
  leading-underscore, module-internal there)** — matches this codebase's
  existing per-stage-duplication convention (no shared `conftest.py`
  either, same spirit). Anti-fabrication is enforced mechanically, not
  just prompt-requested: a `referenced_files` entry absent from the real
  evidence-file set is treated as a retry-worthy failure exactly like a
  JSON-shape validation error, with a corrective retry prompt naming the
  fabricated file(s) — `explain_impact` either returns a fully-verified
  narrative or raises, never a narrative built from a fabrication.
- **Decision — a real circular import was discovered and fixed while
  wiring `mcp/server.py` to `impact.analyzer`.** `codebase_rag_mcp/mcp/
  __init__.py` eagerly imported `mcp.server` (to re-export `run`) — but
  Python always executes a package's `__init__.py` before any of its
  submodules, so the moment `impact/models.py` needed `mcp.models
  .SearchHit`, importing `codebase_rag_mcp.mcp.models` transitively
  forced `mcp/__init__.py` to load `mcp.server`, which now imports
  `impact.analyzer` — a genuine cycle. Fixed by removing `mcp/__init__.py`'s
  eager `run` re-export entirely (it now carries no imports, doc-only);
  `cli/main.py`'s lazy `serve` import was changed to `from codebase_rag_mcp
  .mcp.server import run` directly. No other caller depended on the old
  `from codebase_rag_mcp.mcp import run` path.
- **Consequences:** `find_symbol`'s docstring and `mcp/server.py`'s
  module docstring no longer say "reference tracking is Day 10 scope" /
  "four tools" — both are stale phrasing this day retires.
  `repository_summary` (V2) remains unbuilt, Day 11 scope. Full-path
  import resolution's own limitations (name-based, not a real module
  resolver) mean a sufficiently unusual monorepo/path-alias setup can
  still miss or misattribute an importer — accepted, named V2 scope, not
  revisited here.
