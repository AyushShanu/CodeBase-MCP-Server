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
