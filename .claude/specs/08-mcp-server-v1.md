# Spec: MCP Server (Version 1)

## Overview
Days 02–07 built a complete, independently-tested pipeline (ingestion →
chunking → vector/BM25 indexing → hybrid retrieval → reranking → citation-
backed generation) that nothing outside `tests/` and ad-hoc smoke scripts has
ever called end-to-end. Day 08 is where that pipeline becomes an actual MCP
server Claude Code/Claude Desktop can connect to over stdio and query for
real. Four things make this day larger than "wire four functions together":

1. **The Day 01 server stub is broken against the installed SDK.** The
   installed `mcp` package is `2.0.0` (pyproject.toml only pins `mcp>=1.0`);
   its low-level `mcp.server.Server` no longer has the `@server.list_tools()`
   /`@server.call_tool()` decorators the Day 01 stub and `tests/test_mcp_server.py`
   use — confirmed by running both against the real installed package
   (`AttributeError: 'Server' object has no attribute 'list_tools'`), and
   already visible as 3 pre-existing `mypy`/`pytest` failures on `main` that
   predate this spec. The SDK now exposes a higher-level `mcp.server.MCPServer`
   (this version's equivalent of the old `FastMCP`, which the SDK's own v2
   migration notes describe as a rename — `mcp.server.fastmcp` →
   `mcp.server.mcpserver`/`MCPServer` — not a new, unrelated concept) with a
   `@server.tool()` decorator that derives JSON Schema **and** structured
   output from a plain Python function's type hints/docstring/Pydantic return
   type — verified directly against the installed package (see DECISIONS.md
   D-023). This day replaces the stub with a real server built on
   `MCPServer`, not a patch to the broken low-level API.
2. **Index/model lifecycle caching, explicitly deferred to this day by
   Days 05/06's own `DECISIONS.md`/`FLOW.md` entries.** `hybrid_search` and
   `rerank` each load/construct a fresh index or `CrossEncoder` on *every
   call* today, with their own docstrings and D-020/D-021 saying "no caching
   yet — Day 08's MCP server owns index lifecycle once it exists." D-021
   measured `CrossEncoder` construction at ~9.7–9.9s per call — reconstructing
   it per tool call would make every single `search_code`/`ask` invocation
   pay that cost. This day is where that caching actually gets built.
3. **Day 07's own flagged gap**: CLAUDE.md's MCP Tool Contract table has no
   tool that exposes a generated answer (`search_code` returns "ranked code
   evidence," not prose), yet CLAUDE.md's architecture diagram and Day 07's
   own roadmap line clearly intend server-side generation feeding the four
   Non-Negotiable Demo Questions, and `FLOW.md`'s Section 3 was already
   titled "Online query flow (`ask` tool)" before this day started — the
   project's own documentation had already named the resolution. **Decision
   for this day: add a fourth tool, `ask`, wiring the full pipeline through
   `generation.pipeline.generate_answer`, alongside the three V1-contract
   tools (`search_code`, `find_symbol`, `get_file_context`) which return raw
   evidence only.** This is a deliberate addition beyond CLAUDE.md's literal
   table, logged to `DECISIONS.md` and reflected back into CLAUDE.md's own
   table rather than left silently inconsistent.
4. **This is the first day the server runs as a long-lived, protocol-
   sensitive process rather than a short-lived script or test run — two
   new classes of risk that only exist at that point, added explicitly by
   this revision, not left implicit:** (a) stdio's protocol stream requires
   stdout to carry *only* JSON-RPC traffic, and every ML-heavy library
   already in this codebase (`sentence-transformers`, `transformers`,
   `huggingface_hub`) is known to print progress/warning noise to stdout on
   first model use — exactly what this day's startup caching triggers for
   the first time inside a live server process; (b) a shared, cached
   `CrossEncoder`/index instance reused across every tool call introduces a
   concurrency question (is it safe to call from two tool invocations at
   once?) that never existed when each call built its own fresh instance.
   Both are addressed explicitly below rather than left to be discovered
   against a real client.

`find_symbol` also needs an honest scope note: CLAUDE.md describes it as
finding a symbol "and its definition/usages," but no reference/caller model
exists until Day 10 ("Symbols, References & Impact Analysis"). V1's
`find_symbol` returns **definition locations only** (chunks whose indexed
`symbol` field matches) — this is stated in the tool's own description, not
just this spec, so the limitation is visible to whoever is using it.

`get_file_context` also needs an honest scope note, new to this revision:
it resolves `file` against the manifest's `repo_root` and reads whatever
real file exists there, **not** restricted to files that Day 02's ingestion
filters actually included in the index. CLAUDE.md's own Day 11 line names
"secret-file exclusion" as planned, unbuilt V2 scope — meaning a checked-out
repo can contain a file (e.g. a committed `.env`) that was correctly
excluded from embedding/chunking but is still physically present under
`repo_root` and therefore readable through this tool today. This is an
accepted, named V1 limitation (see "Rules for implementation" and
`DECISIONS.md`), not a silent gap — full secret-file awareness stays Day 11
scope, same deferral pattern Day 07 used for prompt-injection hardening.

## Depends on
- Day 01 — Foundation. The package/CLI scaffold exists but its MCP server
  stub must be replaced, not extended (see above).
- Day 02 — GitHub Ingestion & File Filtering (`ingestion.loader.load_repo`,
  `RepoSource.root`/`.original`). Complete. This day is the first to
  actually invoke ingestion from the CLI (`codebase-rag index`) rather than
  only from tests/manual scripts. Its symlink-containment discipline for
  file reads is the direct precedent `get_file_context`'s own containment
  check must follow (see "Rules for implementation").
- Day 03 — Tree-sitter Parsing & AST Chunking (`chunker.models.Chunk`,
  qualified `ClassName.method` naming per D-013). Complete.
- Day 04 — Embeddings & Vector Index (`indexing.vector.load_index`,
  `VectorIndex.chunks`). Complete.
- Day 05 — BM25 & Hybrid Retrieval (`retrieval.hybrid.hybrid_search`).
  Complete per `.claude/specs/05-hybrid-retrieval.md`. This day extends its
  signature additively (see "Files to change") — it does not change the RRF
  merge logic itself.
- Day 06 — Reranker (`reranker.rerank.rerank`). Complete per
  `.claude/specs/06-reranker.md`. Same additive-extension treatment as
  `hybrid_search`. **Callers still must pass `top_k=HYBRID_CANDIDATE_POOL_SIZE`
  into `hybrid_search` before reranking** — this day's `search_code`/`ask`
  tool implementations are the first real production callers required to
  honor that D-021 calling contract, not just tests.
- Day 07 — LLM Generation & Citations (`generation.pipeline.generate_answer`,
  `generation.models.GeneratedAnswer`, `citations.format.format_citations_markdown`).
  Complete per `.claude/specs/07-llm-generation-citations.md`. Consumed
  as-is by the new `ask` tool — no changes to `generation/`/`citations/`.

## Pipeline stage(s) touched
- **MCP Server** (primary — full rewrite of the Day 01 stub).
- **Hybrid Retrieval** (additive only — `hybrid_search` gains optional
  pre-loaded-index parameters; RRF logic untouched).
- **Reranking** (additive only — `rerank` gains an optional pre-built
  `CrossEncoder` parameter; scoring logic untouched).
- **Embedding/BM25 indexing** (additive only — `indexing.repo.build_all_indexes`
  also writes a small per-index manifest; no change to how chunks are
  embedded/tokenized/persisted).

Generation and Citations are consumed unchanged.

## MCP tools affected
Adds all four V1 tools for the first time (the Day 01 stub only ever
advertised a placeholder `ping`):
- `search_code(query, top_k)` — hybrid retrieval + rerank, returns ranked
  evidence (file/symbol/line/content/score). Matches CLAUDE.md's V1 contract
  literally.
- `find_symbol(symbol)` — exact/qualified-suffix match against indexed
  chunk symbol names, returns matching definition locations. **Definitions
  only, not usages** (see Overview) — this narrowing is stated in the tool's
  own description.
- `get_file_context(file, start_line, end_line)` — exact verbatim source
  lines for a file in the indexed repo's checkout, resolved via the new
  per-index manifest. **Not restricted to indexed files** — see Overview's
  scope note.
- `ask(query)` — **new, not in CLAUDE.md's current table.** Full pipeline:
  `hybrid_search` (wide pool) → `rerank` → `generate_answer` → citations.
  Returns `generation.models.GeneratedAnswer` directly (no wrapper needed —
  it's already tool-response-shaped: `answer`, `citations`, `has_sufficient_evidence`,
  `provider_used`). **This addition must be reflected back into CLAUDE.md's
  "MCP Tool Contract" table** (new row, version "V1") — see "Files to
  change" — rather than leaving the roadmap document silently out of sync
  with what's actually built.

`analyze_impact`/`repository_summary` (V2) are out of scope — Day 10/11.

## Model / provider choice for this step
No new model/provider row is chosen this day. This day reuses, unchanged:
Day 04's `all-MiniLM-L6-v2` embeddings, Day 06's
`cross-encoder/ms-marco-MiniLM-L-6-v2` reranker, and Day 07's five-provider
generation fallback chain. The only lifecycle decision this day makes is
**caching**: the vector index, BM25 index, and `CrossEncoder` are each
loaded/constructed exactly once, at server startup, and reused for the
process's lifetime — via `MCPServer`'s `lifespan` async-context-manager
mechanism (confirmed present in the installed SDK; tool functions access
the cached state by declaring a `Context` parameter, per the SDK's own
documented pattern — see DECISIONS.md D-023 for the exact mechanism
confirmed at implementation time). This directly fulfills what D-020/D-021
explicitly deferred to "Day 08's MCP server" rather than reconstructing a
~10-second `CrossEncoder` on every tool call.

**A version-pin decision this day must also make and log:** `pyproject.toml`
currently declares `mcp>=1.0`, which is how a 1.x-shaped low-level API ended
up scaffolded against a 2.0-shaped installed package with no version error
ever surfacing. Tighten to `mcp>=2.0,<3.0` (mirroring D-014's precedent of
pinning `tree-sitter` after finding a real regression) so a future `mcp`
major bump fails loudly (a version conflict at install time) rather than
silently reintroducing this same class of breakage.

**Two runtime-safety decisions this revision adds explicitly:**
- **stdout/stderr discipline:** the server must configure Python `logging`
  to write exclusively to `stderr` before any pipeline module is imported or
  any model is loaded, and must suppress/redirect known stdout-writing
  third-party noise from the ML dependencies already in this codebase
  (e.g. Hugging Face Hub's progress bars via `HF_HUB_DISABLE_PROGRESS_BARS`,
  `transformers`' console verbosity setting) — set at process startup, not
  left to library defaults. This is not optional polish: stdio's JSON-RPC
  stream is on stdout, and this is the first day model loading happens
  inside that live process rather than a test harness or one-off script.
- **Concurrency safety for cached state:** the cached `VectorIndex`,
  `Bm25Index`, and `CrossEncoder` instances are shared across every tool
  call for the server's lifetime. Guard access to them with an `asyncio.Lock`
  held inside `mcp/server.py`'s lifespan-managed state (not pushed into
  Day 05/06's own functions, which stay synchronous and unaware of the
  server's concurrency model) so two tool calls arriving close together
  cannot call into the same `CrossEncoder`/index concurrently. Log the
  reasoning to `DECISIONS.md` even if empirical testing later shows the
  MCP clients actually used serialize requests in practice — state it as a
  deliberate decision, not an unconsidered gap.

## Files to change
- `src/codebase_rag_mcp/mcp/server.py` — full rewrite onto `mcp.server.MCPServer`.
  Registers `search_code`/`find_symbol`/`get_file_context`/`ask` (the `ping`
  stub is removed, not kept alongside). Configures logging to `stderr` and
  sets the stdout-suppression environment/library flags **before** importing
  any pipeline module. Adds a `lifespan` async context manager that: (1)
  checks `INDEX_DIR` has at least one of the vector/BM25 indexes built — if
  neither exists, fails server startup with a clear, actionable message ("no
  index found under `<INDEX_DIR>` — run `codebase-rag index <repo>` first"),
  mirroring `hybrid_search`'s own "raise only if both missing" semantics but
  surfaced at startup instead of per-query; (2) loads whichever of
  `vector.load_index`/`bm25.load_index` succeeds; (3) constructs one
  `CrossEncoder(RERANKER_MODEL_NAME, max_length=RERANKER_MAX_LENGTH)`; (4)
  attempts to load `indexing.manifest.load_manifest(index_dir=INDEX_DIR)` for
  `get_file_context` (optional — its absence disables `get_file_context` with
  a clear per-call error, not a startup failure, since older/manifest-less
  indexes still work for the other three tools); (5) creates one
  `asyncio.Lock` guarding access to the cached index/`CrossEncoder` state.
  Tool functions receive this cached state via an injected `Context`
  parameter (SDK-documented pattern) and pass the cached index/`CrossEncoder`
  instances into `hybrid_search`/`rerank`'s new optional parameters — never
  constructing their own — acquiring the shared lock around that access.
- `src/codebase_rag_mcp/mcp/__init__.py` — export whatever `cli/main.py`'s
  `serve` subcommand needs from the rewritten server (mirrors today's `run`
  export).
- `src/codebase_rag_mcp/cli/main.py` — add an `index <source>` subcommand:
  `ingestion.loader.load_repo(source)` → `indexing.repo.build_all_indexes(repo_source.root, index_dir=..., repo=...)`, printing basic stats. This is the
  minimum needed to make the MCP server's own Definition of Done runnable
  end-to-end — not full CLI polish (flags, progress bars, incremental
  re-indexing), which stays Day 11 scope as CLAUDE.md already states.
- `src/codebase_rag_mcp/retrieval/hybrid.py` — add optional
  `bm25_index: Bm25Index | None = None` and `vector_index: VectorIndex | None
  = None` parameters to `hybrid_search`. When given, `hybrid_search` uses
  them directly instead of calling `bm25.load_index`/`vector.load_index`
  internally; when omitted (`None`, the default), behavior is byte-for-byte
  identical to today — **every existing test in `tests/test_retrieval_hybrid.py`
  must keep passing unmodified.** The RRF merge, the `score > 0.0` vector
  floor, and `NoIndexAvailableError`'s semantics are untouched.
- `src/codebase_rag_mcp/reranker/rerank.py` — add an optional
  `cross_encoder: CrossEncoder | None = None` parameter to `rerank`. When
  given, skips `CrossEncoder(model_name, max_length=max_length)` construction
  and calls `.predict()` on the given instance instead; when omitted,
  behavior is byte-for-byte identical to today — **every existing test in
  `tests/test_reranker.py` must keep passing unmodified.**
- `src/codebase_rag_mcp/indexing/repo.py` — `build_all_indexes` also calls
  `indexing.manifest.write_manifest(index_dir=index_dir, repo_root=root,
  source=...)` after building both indexes, so the manifest and the indexes
  it describes are always written together from the one orchestration entry
  point D-020 already established (never two separate write paths that
  could disagree).
- `pyproject.toml` — tighten `mcp>=1.0` to `mcp>=2.0,<3.0`.
- `CLAUDE.md` — add an `ask` row to the "MCP Tool Contract" table
  (`Purpose: "Full RAG pipeline: retrieve, rerank, and generate a
  citation-backed answer"`, `Version: V1`), so the roadmap document reflects
  what Day 08 actually ships rather than staying silently out of sync.
- `FLOW.md` — Section 1's component map already sketches `TOOLS[Tool
  handlers<br/>search · impact · ask]`; update it to the four real V1 tool
  names (`search_code`, `find_symbol`, `get_file_context`, `ask` — `impact`
  is Day 10, not this day). Section 3 ("Online query flow (`ask` tool)")
  gets its placeholder participants (`M as MCP server`) wired to the real
  `mcp/server.py` tool functions and the lifespan-caching design (including
  the stdout/stderr and lock decisions); add a short new flow description
  for `search_code`/`find_symbol`/`get_file_context` alongside the existing
  `ask` sequence diagram, since those three don't call `generate_answer`.
- `tests/test_mcp_server.py` — full rewrite. The existing three tests
  exercise the now-nonexistent low-level `Server.list_tools()`/`call_tool()`
  API and already fail on `main` (pre-existing, unrelated to this spec) —
  they are replaced, not patched.
- `tests/test_retrieval_hybrid.py` — add cases for the new optional
  `bm25_index`/`vector_index` parameters (pre-loaded instance is used
  in-place of loading fresh; omitting them preserves today's behavior).
- `tests/test_reranker.py` — add cases for the new optional `cross_encoder`
  parameter, mirroring the same "used when given, defaults preserve
  today's behavior" shape.
- `tests/test_indexing_repo.py` — extend to assert `build_all_indexes`
  writes a valid manifest alongside the two indexes.

## Files to create
- `src/codebase_rag_mcp/mcp/models.py` — Pydantic (frozen, matching every
  other stage's convention) MCP-facing response models: `SearchHit` (`file`,
  `symbol`, `language`, `start_line`, `end_line`, `content`, `score` —
  flattened from `RerankedResult`/`Chunk` the same way Day 07's `Citation`
  deliberately flattened `Chunk`, for the same reason: a tool response is a
  small external-facing summary, not an internals-inspection object),
  `SearchCodeResult` (`query`, `hits: list[SearchHit]`), `FindSymbolResult`
  (`symbol`, `matches: list[SearchHit]`), `FileContextResult` (`file`,
  `start_line`, `end_line`, `content`). `ask`'s return type is
  `generation.models.GeneratedAnswer` directly — no new wrapper model.
- `src/codebase_rag_mcp/mcp/exceptions.py` — `MCPServerError` base;
  `IndexNotAvailableError` (raised by the `lifespan` startup check when
  neither index exists); `RepoRootUnknownError` (raised by `get_file_context`
  when no manifest was written for this index); `PathOutsideRepoRootError`
  (raised by `get_file_context` when the resolved path escapes the
  manifest's `repo_root` — the one cheap containment check this day adds,
  mirroring Day 07's "one cheap mitigation now, full hardening later"
  pattern; CLAUDE.md's Day 11 explicitly scopes full "path restriction"
  hardening, not rebuilt here); `InvalidLineRangeError` (raised by
  `get_file_context` for `start_line < 1` or `start_line > end_line` or
  `start_line` beyond the file's actual length — `end_line` beyond the
  file's length is clamped to the last line instead of erroring, a
  deliberately lenient case stated explicitly in the tool's docstring).
- `src/codebase_rag_mcp/indexing/manifest.py` — `IndexManifest` (frozen
  Pydantic: `repo_root: str`, `source: str`), `MANIFEST_FILENAME =
  "manifest.json"`, `write_manifest(index_dir, *, repo_root: Path, source:
  str) -> None`, `load_manifest(index_dir) -> IndexManifest | None` (returns
  `None`, never raises, when the manifest file doesn't exist — an
  older/manifest-less index is a normal, expected case for the three tools
  that don't need it, not an error).
- `tests/test_indexing_manifest.py` — unit tests for `write_manifest`/
  `load_manifest`: round-trip, missing-file returns `None`, malformed JSON
  is handled per the same "translate to a typed result, don't crash the
  caller" discipline as `indexing.vector.load_index`.

## New dependencies
No new dependencies and no new API key. The installed `mcp` package
(`2.0.0`) already satisfies a tightened `mcp>=2.0,<3.0` constraint — this is
a version-pin correction, not a new package.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. (Unaffected this day; `get_file_context` reads verbatim source
  lines directly from disk for an *exact* requested range, which is
  independent of chunk boundaries by design — it must not attempt to
  reconstruct a range from indexed chunk content.)
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata. `SearchHit`/`FindSymbolResult` entries and
  `FileContextResult` must all carry real file/line data sourced from
  indexed `Chunk`/manifest data — never approximate or omitted.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess. `ask`
  inherits this unmodified from `generate_answer` — the MCP layer must not
  add its own prompting or attempt to "improve" a `has_sufficient_evidence:
  false` result.
- All LLM calls go through the provider fallback chain defined in CLAUDE.md
  — never hardcode a single provider. `ask` calls `generate_answer` as-is;
  no direct provider call is ever made from `mcp/`.
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose. All new MCP-facing models (`mcp/models.py`) are Pydantic, matching
  every other stage's convention, so `MCPServer`'s automatic schema/
  structured-content generation (confirmed against the installed SDK) works
  for every tool without hand-written JSON Schema.
- Embedding and reranker models run locally by default — no paid API in the
  default path. Unaffected — this day only changes *when* the local
  `CrossEncoder`/embedding model get constructed (once, at startup), never
  *whether* they run locally.
- **No MCP tool function constructs its own `VectorIndex`, `Bm25Index`, or
  `CrossEncoder`.** All three are built exactly once by the `lifespan`
  context manager and passed into `hybrid_search`/`rerank` via their new
  optional parameters — directly tested (a spy/counter proves a second
  `search_code`/`ask` call does not re-load or re-construct any of the
  three).
- **`hybrid_search`'s and `rerank`'s new optional parameters must default to
  `None` and be strictly additive** — every existing call site and test
  from Days 05/06/07 must keep working, and keep its exact current
  behavior, unmodified.
- **Callers of `hybrid_search` that intend to rerank must still pass
  `top_k=HYBRID_CANDIDATE_POOL_SIZE`** (D-021's calling contract) — this
  applies to `search_code` and `ask`'s implementations directly; getting
  this wrong silently degrades retrieval quality without raising anything.
- `find_symbol` must state its "definitions only, not usages" limitation in
  its own tool description string (visible to whatever MCP client/user
  calls it), not only in this spec or `DECISIONS.md`. Zero matches returns
  an empty `matches` list, never an error — mirrors `hybrid_search`/`rerank`'s
  own "empty is a valid result" convention.
- **`get_file_context` must resolve `file` against the manifest's
  `repo_root` using `Path.resolve()` on the full joined path — never a
  string-prefix comparison on the unresolved path** — then verify the
  *resolved* absolute path is still contained within `repo_root`'s own
  resolved form (reject any traversal attempt, including one that only
  becomes visible after resolving an intermediate symlink) before ever
  opening the file, and read the exact requested line range from the real
  file on disk. Never reconstruct a range from indexed chunk `content`, and
  never read any path the containment check doesn't clear. This mirrors
  Day 02's symlink-escape discipline in `ingestion/scanner.py`, applied
  fresh here since this is new, separate file I/O at query time, not a
  reuse of Day 02's already-tested code path.
- **`get_file_context`'s scope is deliberately "any real file under
  `repo_root`," not "any file that was actually indexed."** This is a
  named, accepted V1 limitation (see Overview) — do not attempt to
  cross-check the requested `file` against indexed chunks or Day 02's
  filter decisions this day; that's out of scope until Day 11's
  "secret-file exclusion" work. Log the limitation explicitly to
  `DECISIONS.md` so it is a stated, deliberate scope boundary, not a gap
  nobody noticed.
- **stdout is reserved for the MCP JSON-RPC stream — nothing else may
  write to it.** Configure `logging` to `stderr` and suppress known
  stdout-writing third-party output from the ML dependencies (Hugging Face
  Hub progress bars, `transformers` verbosity) before the server begins
  accepting connections, and before any pipeline module that could trigger
  model loading is imported. This must be verified, not assumed — see
  Definition of Done.
- **The cached `VectorIndex`/`Bm25Index`/`CrossEncoder` instances are
  guarded by a single `asyncio.Lock` owned by `mcp/server.py`'s lifespan
  state**, acquired by every tool call before touching that shared state.
  Day 05/06's own functions stay synchronous and lock-unaware; the
  concurrency guarantee is entirely the MCP layer's responsibility.
- The MCP server must fail fast at startup (not on the first tool call) if
  no index exists at all under `INDEX_DIR`, with a message that tells the
  operator exactly what command to run to fix it.
- The `ask` tool addition and the `mcp>=2.0,<3.0` pin correction are both
  deliberate deviations from what CLAUDE.md/`pyproject.toml` currently say
  — log both to `DECISIONS.md` and reflect the `ask` addition back into
  CLAUDE.md's own Tool Contract table, per the project's "don't silently
  re-decide/diverge from what's already stated" discipline.
- Log every meaningful decision to `DECISIONS.md` — in particular: the
  `mcp` SDK version drift and the low-level-`Server`-to-`MCPServer`
  migration (with the exact broken/working API shapes confirmed against
  the installed package), the `ask` tool addition and the CLAUDE.md table
  amendment, the startup-caching design (what's loaded once, via what
  mechanism, and why per D-020/D-021), the manifest design (why
  `get_file_context` needs it, why its absence degrades gracefully rather
  than failing the whole server), `get_file_context`'s "any real file under
  repo_root" accepted-limitation, the `mcp` version-pin tightening, the
  stdout/stderr discipline decision, and the `asyncio.Lock` concurrency
  decision.
- Update `FLOW.md` with the new/changed pipeline paths: Section 1's
  component map gets the real four tool names; Section 3 gets the real
  module paths wired in for `ask` plus new coverage for the other three
  tools.

## Definition of done
- [ ] `codebase-rag index <path-or-url>` builds both indexes against the
      real `p-queue` fixture repo (same one Days 03–07 used) and writes a
      valid `INDEX_DIR/manifest.json` whose `repo_root` points at the real
      checkout directory.
- [ ] Starting `codebase-rag serve` with `INDEX_DIR` empty/nonexistent
      fails fast with a clear, actionable error naming the `index` command
      to run — verified by a test, not just manual inspection.
- [ ] `codebase-rag serve` started against the real built `p-queue` index
      advertises exactly `search_code`, `find_symbol`, `get_file_context`,
      `ask` via `list_tools` (no `ping`).
- [ ] A test proves the vector index, BM25 index, and `CrossEncoder` are
      each constructed/loaded exactly once across multiple tool calls in
      the same server lifetime (e.g. two `search_code` calls plus one `ask`
      call trigger exactly one load/construction of each).
- [ ] **A test proves nothing is written to stdout during server startup
      and a tool call that triggers first-time model loading** — capture
      stdout during a startup+call sequence against a cold model cache (or
      simulate one) and assert it is empty; only stderr may carry log/
      progress output. This is the one item in this Definition of Done most
      likely to only fail against a real, uncached environment — do not
      treat "tests pass with a warm model cache" as sufficient evidence
      this is handled.
- [ ] A test proves the `asyncio.Lock` around cached state actually
      serializes concurrent access — e.g. two simulated concurrent calls
      into a slow fake `CrossEncoder`/index prove the second waits for the
      first rather than running interleaved.
- [ ] `search_code` against real `p-queue` queries from
      `benchmarks/questions.json` (one per category: `exact_symbol`,
      `semantic`, `structural`) returns hits whose file/line match the
      benchmark's `expected_file`/`expected_symbol`.
- [ ] `find_symbol("PQueue")` returns the real definition chunk at
      `source/index.ts`; a bare-name query like `"pause"` matches the
      qualified `PQueue.pause` chunk per the suffix-match rule; a
      nonexistent symbol returns `matches: []`, not an error.
- [ ] `get_file_context` returns the exact verbatim lines for a real range
      in the p-queue checkout; a traversal attempt (e.g. a path resolving
      outside `repo_root`, including via a symlink) is rejected with a
      clear `PathOutsideRepoRootError` message, not a raw stack trace or a
      silently-wrong read — tested with both a plain `../` traversal and a
      symlink-based one, per the `.resolve()`-based containment check.
- [ ] `ask` produces a citation-backed answer for CLAUDE.md's four
      Non-Negotiable Demo Questions run against a real indexed repo with at
      least one configured provider (recording which provider served each,
      and an honest `has_sufficient_evidence=False` for any question the
      demo repo genuinely can't answer).
- [ ] Unit tests confirm `hybrid_search`/`rerank`'s new optional parameters
      are purely additive: every pre-existing test in
      `tests/test_retrieval_hybrid.py`/`tests/test_reranker.py` still passes
      unmodified, plus new tests covering the pre-loaded-instance path.
- [ ] `tests/test_indexing_manifest.py` covers round-trip write/load,
      missing-file (`None`, no raise), and malformed-JSON handling.
- [ ] `ruff check`, `ruff format --check`, and `mypy` all pass on every
      changed/new file.
- [ ] **Manual end-to-end connection test**: configure Claude Code or
      Claude Desktop to launch `codebase-rag serve` over stdio against the
      real indexed `p-queue` repo, and successfully invoke at least one
      real tool call from the client (not just the in-process test suite).
      Run this against a machine/environment where the embedding/reranker
      models are **not** already cached locally, specifically to exercise
      the stdout-discipline item above under real first-download
      conditions, not a warm cache that would hide the issue.
- [ ] `DECISIONS.md` has new entries covering the `mcp` SDK migration, the
      `ask` tool addition (+ the CLAUDE.md table amendment actually made),
      the startup-caching design, the manifest design, the
      `get_file_context` "any real file under repo_root" accepted
      limitation, the version-pin correction, the stdout/stderr discipline
      decision, and the `asyncio.Lock` concurrency decision; `CLAUDE.md`'s
      MCP Tool Contract table includes the new `ask` row; `FLOW.md`'s
      Section 1 and Section 3 reflect the real, now-implemented tool
      wiring.