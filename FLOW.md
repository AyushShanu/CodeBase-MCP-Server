# FLOW

> End-to-end data flow for `codebase-rag-mcp`. Diagrams are intentionally
> Mermaid so they render in any Markdown viewer, GitHub, or VS Code.

---

## 1. Component map

```mermaid
flowchart LR
    subgraph Client
        IDE[IDE / MCP client]
    end

    subgraph Server[codebase-rag-mcp server]
        MCP[MCP stdio layer<br/>mcp/server.py]
        TOOLS[Tool handlers<br/>search_code · find_symbol · get_file_context · ask · analyze_impact · repository_summary]
        RETRIEVAL[Retrieval]
        RERANKER[Reranker]
        GEN[Generation]
        IMPACT[Impact analysis<br/>impact/analyzer.py]
        SUMMARY[Repo summary<br/>impact/summary.py]
    end

    subgraph Indexes[Persisted indexes]
        FAISS[(FAISS vector index)]
        BM25[(BM25 sparse index)]
        META[(Chunk metadata store)]
        REFS[(Reference/import index)]
        CACHE[(Chunk cache<br/>chunk_cache.json)]
    end

    subgraph Providers[LLM providers]
        NVIDIA[NVIDIA]
        GROQ[Groq]
        OR[OpenRouter]
        GEM[Gemini]
        LOCAL[Local OpenAI-compatible]
    end

    IDE <-->|JSON-RPC over stdio| MCP
    MCP --> TOOLS
    TOOLS --> RETRIEVAL
    RETRIEVAL --> RERANKER
    RETRIEVAL --> FAISS
    RETRIEVAL --> BM25
    RETRIEVAL --> META
    RERANKER --> GEN
    GEN --> NVIDIA
    GEN --> GROQ
    GEN --> OR
    GEN --> GEM
    GEN --> LOCAL
    TOOLS --> IMPACT
    IMPACT --> REFS
    IMPACT --> GEN
    TOOLS --> SUMMARY
    SUMMARY --> META
    SUMMARY --> GEN
```

---

## 2. Offline build flow

`codebase-rag index <path>` will walk the corpus once, persist indexes
to `INDEX_DIR`, and exit. Subsequent `codebase-rag serve` invocations
load the persisted indexes instead of rebuilding them.

Ingestion (`ingestion/loader.py` → `ingestion/filters.py` /
`ingestion/languages.py` → `ingestion/scanner.py`) accepts either an
`https://` GitHub URL — shallow-cloned via `subprocess` + the system
`git` into `DATA_DIR/clones/` — or a local directory path, then walks
the checkout applying ignore rules and extension-based language
detection to produce the `RepoStats` / `FileRecord` list consumed by
the parser stage below.

Parsing and chunking (`parser/extractor.py:parse_file` →
`chunker/chunker.py:chunk_file`, using `chunker/fallback.py:
split_oversized_symbol` for any symbol whose span exceeds
`DEFAULT_MAX_CHUNK_LINES`) each operate on **one file at a time** — given
a `FileRecord.path`/`language` and that file's raw bytes, `parse_file`
resolves a Tree-sitter grammar (`parser/grammars.py`, `.tsx` routed to
the `"tsx"` grammar even though ingestion reports it as `"typescript"`)
and hand-walks the AST into a `ParseResult` (functions/classes/methods/
interfaces with 1-indexed line ranges, qualified `ClassName.method`
names for nested symbols); `chunk_file` then decodes the file's bytes to
text exactly once and slices one `Chunk` per symbol (or a single
whole-file fallback `Chunk` if a file has none).

Repo-wide orchestration (`indexing/repo.py:collect_repo_chunks`) is the
first place `parse_file`/`chunk_file` get looped over an entire
`RepoStats`: for every `included=True` `FileRecord`, it reads
`root / file.path`, calls `parse_file` (with the repo-relative, not
physical, path -- see D-018) then `chunk_file`, and aggregates every
file's chunks into one `list[Chunk]` for the whole repo. A file that
can't be read (`OSError`), or whose language has no configured
Tree-sitter grammar yet (`UnsupportedLanguageError`) or that Tree-sitter
can't parse at all (`ParseError`), is recorded into
`RepoChunkCollection.read_failures` (path + reason) rather than aborting
the run -- see D-018 for why the language/parse-error case matters in
practice (most real repos have at least a README.md, which has no
grammar configured today).

Embedding (`indexing/vector.py:embed_chunks` → `embed_texts` →
`build_index`) takes that aggregated `list[Chunk]`, filters out any
chunk with empty/whitespace-only `content` (recorded as a `SkippedChunk`,
never silently dropped), and embeds the rest locally via
`langchain_huggingface.HuggingFaceEmbeddings` (`all-MiniLM-L6-v2` by
default, see `config.EMBEDDING_MODEL_NAME`/`EMBEDDING_BATCH_SIZE`) in a
single batched call per `embed_texts` invocation (D-017). Every resulting
vector is L2-normalized in one place (`_l2_normalize`) so the persisted
FAISS `IndexFlatIP` (D-015/D-016) performs a correct cosine-similarity
search — the same normalization path is reused at query time
(`VectorIndex.query`) so build-side and query-side vectors are always
comparable.

BM25 indexing (`indexing/bm25.py:build_index`) takes the same aggregated
`list[Chunk]` — via `indexing/repo.py:build_all_indexes`, which calls
`collect_repo_chunks` exactly once and hands the identical chunk list to
both `vector.build_index` and `bm25.build_index`, so the two indexes always
share the same chunk set and `Chunk.id` values (D-020). Chunks are
tokenized (lowercase, split on non-alphanumeric runs — one shared
`tokenize` function, reused at query time) into a `rank_bm25.BM25Okapi`
corpus and persisted as a pickle (`bm25.pkl`, since `BM25Okapi` has no
native serialization) alongside a JSON chunk-metadata sidecar
(`bm25_metadata.json`), mirroring the vector index's binary+JSON split.

Reference indexing (`indexing/references.py:build_index`/`write_index`,
Day 10) is a parallel, sibling path fed by the *same* per-file
`parser.extractor.parse_file` call `collect_repo_chunks` already makes —
`parse_file` now also runs a second, full-tree Tree-sitter walk (opposite
recursion policy from the symbol walk: it descends into every function/
method body, since that's where calls happen) to produce
`ParseResult.references: list[RawReference]` (call sites + import
statements), which `collect_repo_chunks` tags with the originating file
into `RepoChunkCollection.references: list[FileReference]` alongside
`.chunks`. `indexing.repo.build_all_indexes` then groups that list into a
`ReferenceIndex` (a plain in-memory lookup, not a model — `by_name` for
call-site lookup, `imports` for import-statement lookup) and persists it
as plain JSON (`references.json`, no pickle needed) — no re-walk of the
repo, since `collect_repo_chunks` already produced everything needed in
its one pass.

```mermaid
flowchart TD
    A[Target: https:// GitHub URL or local path] --> B1[ingestion.loader<br/>load_repo]
    B1 --> B2{https URL or local path?}
    B2 -->|https URL| B3[git clone --depth 1<br/>subprocess, timeout-enforced]
    B2 -->|local path| B4[validate exists + is directory]
    B3 --> B5[ingestion.scanner<br/>scan]
    B4 --> B5
    B5 --> B6[ingestion.filters<br/>dir / lockfile / binary / size rules]
    B5 --> B7[ingestion.languages<br/>extension to language]
    B6 --> B8[RepoStats + FileRecord list]
    B7 --> B8
    B8 --> C1[parser.grammars<br/>resolve_grammar_name + cached Parser]
    C1 --> C2[parser.extractor<br/>parse_file: Tree-sitter AST walk]
    C2 --> D1[chunker.chunker<br/>chunk_file: decode once, slice per symbol]
    D1 -.oversized symbol.-> D2[chunker.fallback<br/>split_oversized_symbol]
    D2 -.-> D1
    D1 --> D3[indexing.repo<br/>collect_repo_chunks: loop over RepoStats]
    D3 --> E[indexing.vector<br/>embed_chunks -> build_index<br/>all-MiniLM-L6-v2, batched + L2-normalized]
    D3 --> F[indexing.bm25<br/>tokenize -> BM25Okapi build_index]
    D3 --> I[indexing.references<br/>build_index -> write_index<br/>grouped by_name / imports lookup]
    E --> G[("data/index/<br/>vector.faiss + vector_metadata.json")]
    F --> H[("data/index/<br/>bm25.pkl + bm25_metadata.json")]
    I --> J[("data/index/<br/>references.json")]
```

`parse_file`/`chunk_file` are exercised directly by
`tests/test_parser.py`/`tests/test_chunker.py`. `indexing.repo.collect_repo_chunks`
(`D3`) and `indexing.vector` (`E`/`G`) are implemented and tested as of
Day 04 (`tests/test_indexing_repo.py`, `tests/test_indexing_vector.py`) —
a fresh process can `load_index(index_dir=...)` against `G` and query it
with no rebuild. `F`/`H` (BM25) are implemented and tested as of Day 05
(`tests/test_indexing_bm25.py`), reached via
`indexing.repo.build_all_indexes` which calls `collect_repo_chunks` exactly
once and builds `E` and `F` from the identical chunk list (D-020). `I`/`J`
(the reference/import index) are implemented and tested as of Day 10
(`tests/test_indexing_references.py`), reached via the same
`build_all_indexes` call — `references.json`'s absence is lenient (a
fresh `ReferenceIndex | None` load returns `None`, not an error; see
D-024), unlike `E`/`G` and `F`/`H`'s strict "must be built first" contract.

**Incremental indexing (`indexing.repo.build_all_indexes_incremental`,
Day 11)** is the `codebase-rag index` CLI's only entry point as of this
day (`build_all_indexes` above is unchanged and still exists, but is no
longer called from the CLI — see DECISIONS.md D-025) — a cache-aware
sibling with its own per-file hit/miss/deletion branching, feeding the
exact same `E`/`F`/`I` build calls as the diagram above once the
cached-plus-fresh chunk/embedding/reference set is assembled:

```mermaid
flowchart TD
    A2[scan root] --> B9[ChunkCache.load index_dir<br/>never raises -- missing/corrupt = empty cache]
    B9 --> C3{per included file}
    C3 --> D4[hash_bytes file content]
    D4 --> D5{cache.get file, hash}
    D5 -->|hit| D6[reuse cached chunks + embeddings + references<br/>skip parse_file / chunk_file / embed_chunks entirely]
    D5 -->|miss: unknown file or hash changed| D7[_parse_and_chunk_source<br/>same parse_file + chunk_file as D3 above]
    D7 --> D8[accumulate this file's fresh chunks<br/>into ONE combined cross-file list]
    D6 --> E2[after the loop: cache.retain_only included_paths<br/>drops entries for deleted/renamed/newly-filtered files]
    D8 --> E1[exactly ONE vector.embed_chunks call<br/>over every cache-miss file's chunks combined -- D-017 batching preserved]
    E1 --> E3[slice batched (chunks, vectors) back to per-file<br/>ChunkCacheEntry via chunk.id, cache.put each]
    E3 --> E2
    E2 --> F2[cache.write index_dir]
    F2 --> G2[derive full_corpus_chunks + faiss_chunks/vectors<br/>from the cache itself, post-retain_only]
    G2 --> E[[vector.build_index_from_embeddings<br/>same FAISS construction as E above]]
    G2 --> F[[bm25.build_index -- ALWAYS full rebuild, never patched]]
    G2 --> I[[references.build_index -- ALWAYS full rebuild, never patched]]
    F2 -.-> K[("index_dir/<br/>chunk_cache.json")]
```

Two invariants worth stating explicitly (see DECISIONS.md D-025 for the
full reasoning): BM25 and the reference index are **always** fully
rebuilt from the complete current chunk/reference set every run,
incremental or not — BM25's IDF weighting is a whole-corpus quantity a
partial update would silently corrupt, so "incremental" only ever means
skipping the expensive per-file parse/chunk/embed steps, never skipping
index-structure construction itself. And `cache.retain_only` runs before
every `cache.write()`, whether or not `--force` was used — a file that's
disappeared, been renamed, or been newly excluded by a filter rule (e.g.
this same day's secret-file/`data/`-directory exclusion additions) is
dropped from the cache on the very next reindex, not left as a silent
ghost entry. `cli/main.py`'s `--force` flag bypasses `ChunkCache` entirely
(every file is a miss) without deleting `chunk_cache.json`, so a forced
run also naturally repopulates the cache for the next incremental run.

---

## 3. Online query flow

The MCP server (`mcp/server.py`, Day 08 + Day 10's `analyze_impact` +
Day 11's `repository_summary`) owns six tools. `search_code`,
`find_symbol`, and `get_file_context` return raw evidence; `ask` calls
`generate_answer`; `analyze_impact` calls `impact.analyzer.analyze_impact`,
which itself may call `generate_answer`'s sibling
`impact.explain.explain_impact`; `repository_summary` calls
`impact.summary.build_repository_summary`, which may call that module's
own `explain_repository_summary`. All six read cached state
(`vector_index`, `bm25_index`, one `CrossEncoder`, one embedding-model
instance, and — Day 10 — an optional `reference_index`) loaded/constructed
exactly once at server startup via `lifespan` — see D-023/D-024 — never
reconstructed per call, and never touched without holding
`_ServerState.lock` for the calls that actually use it
(`hybrid_search`/`rerank`, and `analyze_impact`'s own `.chunks` read).
`reference_index` is the one piece of that cached state loaded leniently —
its absence (or corruption, logged at startup) never blocks the server
from starting, unlike `vector_index`+`bm25_index` both being absent.

**`serve` startup (Day 12: zero-config auto-indexing):**

```mermaid
flowchart TD
    Start[_lifespan starts] --> Load[_load_ready_indexes: try loading vector/bm25/manifest/references]
    Load --> HasIndex{vector_index or bm25_index loaded?}
    HasIndex -- no --> AutoOn1{auto_index enabled?}
    AutoOn1 -- no --> Raise1[raise IndexNotAvailableError -- unchanged today's behavior]
    AutoOn1 -- yes --> Resolve1[_resolve_effective_source: --repo flag -> REPO_SOURCE env -> cwd if cwd/.git exists]
    Resolve1 --> Resolved1{source resolved?}
    Resolved1 -- no --> Raise1
    Resolved1 -- yes --> Slow[slow path]
    HasIndex -- yes --> AutoOn2{auto_index enabled AND manifest loaded?}
    AutoOn2 -- no --> Fast[fast path -- unchanged latency]
    AutoOn2 -- yes --> Resolve2[_resolve_effective_source, same precedence]
    Resolve2 --> Match{source resolved AND _manifest_matches_source is False?}
    Match -- yes, mismatch --> Slow
    Match -- no, matches or unresolved --> Fast

    Slow --> Schedule[build _ServerState with indexing_status=in_progress, all live fields None; asyncio.create_task schedules _run_auto_index]
    Schedule --> Yield1[yield state immediately -- lifespan never awaits the task]

    Fast --> BuildCE[construct CrossEncoder + embeddings synchronously]
    BuildCE --> Yield2[yield state with indexing_status=ready]
```

```mermaid
sequenceDiagram
    participant U as User (via MCP client)
    participant M as MCP server (a tool call)
    participant BG as _run_auto_index (background asyncio.Task)
    participant TH as worker thread (asyncio.to_thread)

    Note over BG,TH: scheduled by _lifespan's slow path, never awaited by lifespan itself
    BG->>TH: _run_auto_index_build(source, index_dir)
    TH-->>TH: repo_loader.load_repo(source) -- clones an https:// URL, or resolves a local path
    TH-->>TH: indexing_repo.build_all_indexes_incremental(repo_source.root, index_dir=index_dir, repo=source)
    TH-->>TH: repo_source.cleanup() in a finally -- removes the temp clone dir for a URL source, no-op for local, runs whether the build succeeded or failed
    TH-->>TH: _load_ready_indexes(index_dir) -- same reload the fast path uses
    TH-->>TH: construct CrossEncoder + embeddings (deferred here specifically so lifespan's yield was never blocked by their ~9.7-9.9s cost, D-021)
    TH-->>BG: _AutoIndexResult

    U->>M: tools/call (any of the six tools), concurrently, any time
    M->>M: _require_index_ready(state) -- first statement, reads indexing_status/indexing_error under state.lock, releases immediately
    alt indexing_status == "in_progress"
        M-->>U: IndexBuildInProgressError ("retry shortly")
    else indexing_status == "failed"
        M-->>U: AutoIndexError(indexing_error) -- the chained real cause's message
    else indexing_status == "ready"
        M-->>M: proceed with the tool's own logic as normal
        M-->>U: real result
    end

    alt background build succeeded
        BG->>BG: async with state.lock: swap in vector_index/bm25_index/cross_encoder/embeddings/manifest/reference_index, indexing_status="ready"
    else background build raised (any exception)
        BG->>BG: async with state.lock: indexing_status="failed", indexing_error=str(exc) -- never re-raised, so the task never dies silently
    end
```

An already-valid index for the same repo is never auto-refreshed just
because the server restarted (the fast path above) — a deliberate,
permanent limitation (see DECISIONS.md D-026), not solved by this
feature. `_manifest_matches_source` compares differently for a URL vs. a
local `effective_source`, since `IndexManifest.repo_root` is always a
local, resolved path (the checkout root `write_manifest` was called
with) — never the URL itself even for a URL source, whose `repo_root` is
a fresh temp clone dir that differs on every clone.

**`ask` tool:**

```mermaid
sequenceDiagram
    participant U as User (via MCP client)
    participant M as MCP server
    participant R as Retrieval
    participant X as Reranker
    participant G as Generation (pipeline.generate_answer)
    participant P as Provider chain
    participant C as Citations

    U->>M: tools/call { name: "ask", arguments: {query} }
    M->>R: hybrid_search(query, top_k=HYBRID_CANDIDATE_POOL_SIZE, vector_index=state.vector_index, bm25_index=state.bm25_index, embeddings=state.embeddings)
    R-->>R: state.bm25_index.query(query, top_k=candidate_pool_size) -- no bm25.load_index() call, index already cached
    R-->>R: state.vector_index.query(query, top_k=candidate_pool_size, embeddings=state.embeddings) -- no vector.load_index()/HuggingFaceEmbeddings() call either
    R-->>R: filter vector candidates to score > 0.0
    R-->>R: _reciprocal_rank_fusion(bm25_results, vector_results, k=RRF_K)
    R-->>M: wide pool (up to HYBRID_CANDIDATE_POOL_SIZE) HybridQueryResult -- NOT the narrow default top_k=10
    M->>X: reranker.rerank.rerank(query, candidates, cross_encoder=state.cross_encoder)
    X-->>X: state.cross_encoder.predict([(query, c.chunk.content) for c in candidates]) -- no CrossEncoder(...) construction
    X-->>X: sort by rerank_score desc, take top_n (rerank_score and RRF score never combined)
    X-->>M: top_n RerankedResult, each wrapping its full HybridQueryResult
    Note over M: state.lock released here -- generate_answer runs outside it (network call, not shared local-model state)
    M->>G: generate_answer(query, candidates)
    alt candidates is empty
        G-->>M: GeneratedAnswer(insufficient evidence, provider_used=None) -- no provider ever called
    else candidates non-empty
        G-->>G: build system/user prompt (generation.prompts, evidence tagged by chunk_id)
        G->>P: select_providers() -- NVIDIA -> Groq -> OpenRouter -> Gemini -> Local (config-time precedence)
        loop for each configured provider, up to GENERATION_JSON_RETRY_LIMIT+1 attempts
            P-->>P: provider.complete(system, user) -> raw text
            alt ProviderRequestError (incl. timeout)
                P-->>G: fall through to next provider (runtime fallback)
            else raw text returned
                G-->>G: _extract_json_object(raw) -- strip fences / brace-match
                G-->>G: _LLMStructuredOutput.model_validate_json(extracted)
                alt validation fails, attempts remain
                    G-->>G: re-prompt same provider with validation error appended
                else validation succeeds
                    G-->>G: break -- this provider succeeded
                end
            end
        end
        G->>C: attach_citations(cited_chunk_ids, candidates)
        C-->>G: list[Citation], sourced only from candidates' real Chunk metadata
        G-->>G: has_sufficient_evidence = model's claim AND citations non-empty (anti-fabrication override)
        G-->>M: GeneratedAnswer(answer, citations, has_sufficient_evidence, provider_used)
    end
    M-->>U: {answer, citations}
```

`retrieval.hybrid.hybrid_search` (Day 05, `tests/test_retrieval_hybrid.py`)
loads both indexes fresh each call *unless* a caller passes already-loaded
`vector_index`/`bm25_index`/`embeddings` instances (Day 08's additive
caching parameters, D-023) — `mcp/server.py` is the first real caller to do
so, using its startup-cached instances so no per-query `load_index`/
`HuggingFaceEmbeddings()` call (and the network round-trip the latter was
found to trigger) ever happens. Raises `NoIndexAvailableError` only if
*neither* index is available (given or loadable); a query against two
built indexes that matches nothing returns `[]` rather than raising, so a
downstream "not enough evidence" fallback (Day 07) can tell "nothing
indexed" apart from "indexed, no evidence for this query."

`reranker.rerank.rerank` (Day 06, `tests/test_reranker.py`) consumes
`hybrid_search`'s output as-is — it never re-embeds, re-tokenizes, or
re-fetches chunk content, only reorders the `HybridQueryResult`s it is
given. **Callers must pass `top_k=HYBRID_CANDIDATE_POOL_SIZE` into
`hybrid_search` before reranking** — the default `top_k=10` truncates the
merged pool before the reranker ever sees it, defeating the point of this
stage; this is the explicit interface contract between Day 05 and Day 06
(see DECISIONS.md D-021), now honored by `mcp/server.py`'s `search_code`/
`ask` implementations, its first real production callers. `rerank_score`
(a raw cross-encoder logit) and the RRF `score` on the wrapped
`HybridQueryResult` are never combined into a single number — only
`rerank_score` determines the reordered output. `rerank` also accepts a
pre-built `cross_encoder` (Day 08's additive parameter) to skip its own
~9.7-9.9 s construction cost (D-021) per call — `mcp/server.py` constructs
one at startup and reuses it for every tool call in the process's lifetime.

`generation.pipeline.generate_answer` (Day 07, `tests/test_generation.py`)
is the last non-MCP stage. Day 08's `ask` tool calls into this rather than
adding new generation logic of its own (D-022's flagged gap -- no V1
contract tool exposed a generated answer -- is resolved by adding `ask`,
see D-023). It never
calls a provider when `candidates` is empty — zero evidence means zero
tokens spent. Otherwise it builds a strict evidence-only prompt
(`generation.prompts`, each candidate tagged by `chunk_id`) and iterates
`generation.providers.registry.select_providers()`'s configured chain with
**runtime** fallback-on-failure — a `ProviderRequestError` (including a
timeout) or an exhausted `GENERATION_JSON_RETRY_LIMIT` moves to the next
provider; only once every configured provider has failed does
`AllProvidersFailedError` raise, distinct from `NoProviderConfiguredError`
(raised earlier, at `select_providers()`, when nothing was ever a
candidate). A provider's raw text is never trusted directly: it passes
through `_extract_json_object` (defends against code-fence-wrapped or
prose-prefixed JSON, a common free-tier model failure mode) before
`_LLMStructuredOutput.model_validate_json(...)` validates it. The model
only ever supplies `cited_chunk_ids` — `citations.attach.attach_citations`
maps those back to real `Chunk` metadata from `candidates`, silently
dropping any ID absent from the given candidate pool, and a citation list
that comes back empty forces `has_sufficient_evidence=False` regardless of
what the model claimed. See DECISIONS.md D-022 for the full design
rationale, including the confirmed free-tier model per provider and the
named, only-partially-mitigated prompt-injection risk.

**`search_code`, `find_symbol`, `get_file_context` tools (Day 08,
`tests/test_mcp_server.py`)** — none of these three call `generate_answer`;
they return raw evidence only:
- `search_code(query, top_k)`: `hybrid_search` (wide pool, cached indexes)
  → `rerank` (cached `CrossEncoder`, `top_n=top_k`) → flatten each
  `RerankedResult` into a `SearchHit` (file/symbol/line/content/score).
- `find_symbol(symbol)`: reads the cached index's `.chunks` directly (no
  retrieval/reranking at all) and does an exact-or-qualified-suffix match
  on `chunk.symbol` (D-013's `ClassName.method` naming) — `score=1.0`
  exact, `score=0.5` suffix, a match-quality indicator, not a retrieval
  score. Definitions only; see `analyze_impact` below for usages/callers.
  Zero matches is a normal empty result, never an error. This matching
  logic (Day 10) now lives in the shared `impact.symbols.match_symbol_chunks`,
  with `_match_symbol` reduced to a thin `Chunk`→`SearchHit` wrapper
  around it (D-024) — `find_symbol` and `analyze_impact` share exactly
  one implementation.
- `get_file_context(file, start_line, end_line)`: resolves `file` against
  the per-index manifest's `repo_root` (`indexing.manifest`, D-023),
  verifies containment (mirrors `ingestion.scanner.scan`'s resolve-then-
  `relative_to` discipline, raising instead of excluding), and reads the
  exact requested line range directly from disk — never reconstructed from
  indexed chunk content. Works for any real file under `repo_root`, not
  only indexed ones (a named V1 limitation, see D-023).

**`analyze_impact` tool (Day 10, `tests/test_mcp_server.py`,
`tests/test_impact.py`):**

```mermaid
sequenceDiagram
    participant U as User (via MCP client)
    participant M as MCP server
    participant S as impact.symbols
    participant A as impact.analyzer
    participant R as reference_index
    participant X as impact.explain
    participant P as Provider chain

    U->>M: tools/call { name: "analyze_impact", arguments: {symbol} }
    M-->>M: read state.vector_index/bm25_index.chunks under state.lock
    M->>A: analyze_impact(symbol, chunks, state.reference_index)
    A->>S: match_symbol_chunks(symbol, chunks) -- exact + qualified-suffix
    alt zero definitions found
        A-->>M: ImpactResult(has_evidence=False, explanation=None) -- no reference_index access, no LLM call at all
    else at least one definition found
        A-->>A: build definitions: list[SearchHit] (score 1.0 exact / 0.5 suffix)
        alt reference_index is None
            A-->>A: callers=[] importers=[] (index absent -- degrade, don't error)
        else reference_index present
            A->>R: by_name(symbol), kind==CALL -- direct callers
            A-->>A: count_distinct_definitions(bare_trailing_name) -> CONFIRMED (<=1) vs LIKELY (>1)
            A-->>A: resolve each caller's containing chunk -> caller_symbol (#partN-stripped, None for whole-file-fallback/no-containing-chunk)
            A-->>A: is_likely_test(file) path-segment/filename heuristic
            A->>R: imports -- IMPORT-kind entries
            A-->>A: _resolves_to: full-path candidate first, basename fallback only if none buildable -- all importers LIKELY
            A-->>A: cap callers/importers at MAX_IMPACT_REFERENCES_PER_KIND, set *_truncated flags
        end
        A->>X: explain_impact(symbol, partial_result) -- only once real evidence exists
        X->>P: select_providers() -- same NVIDIA -> Groq -> OpenRouter -> Gemini -> Local chain as ask
        X-->>X: extract-JSON-then-validate loop (generation.pipeline's pattern, duplicated locally)
        X-->>X: reject any referenced_files entry absent from real evidence -- retry, same as a JSON-validation failure
        alt every provider fails or none configured
            X-->>A: raises NoProviderConfiguredError / AllProvidersFailedError
            A-->>A: catch, log warning, explanation=None -- deterministic evidence still returned (D-024, diverges from ask's hard-fail)
        else a provider succeeds
            X-->>A: verified narrative string
        end
        A-->>M: ImpactResult(has_evidence=True, ...)
    end
    M-->>U: ImpactResult
```

Unlike `search_code`/`ask`, only the `chunks` read happens under
`state.lock` — the deterministic-evidence assembly and the LLM call both
run outside it, mirroring `ask`'s own `generate_answer` call (a network
call must never block `search_code`/`find_symbol`). See D-024 for the
full design rationale, including the empirically-verified Tree-sitter
node types the reference scanner relies on and the real false-positive
bug (a `.js`-extensioned relative TS import) found and fixed during this
day's own manual verification against the real `p-queue` demo repo.

**`repository_summary` tool (Day 11, `tests/test_mcp_server.py`,
`tests/test_repository_summary.py`):**

```mermaid
sequenceDiagram
    participant U as User (via MCP client)
    participant M as MCP server
    participant Y as impact.summary
    participant P as Provider chain

    U->>M: tools/call { name: "repository_summary", arguments: {} }
    M-->>M: read state.vector_index/bm25_index.chunks under state.lock
    M->>Y: build_repository_summary(chunks)
    alt chunks is empty
        Y-->>M: RepositorySummaryResult(zeroed fields, explanation=None) -- no LLM call at all
    else chunks non-empty
        Y-->>Y: aggregate total_files/total_chunks/languages (dict[str,int])
        Y-->>Y: distinct_symbol_count via impact.symbols.count_distinct_definitions -- never a raw per-chunk count (avoids #partN double-counting)
        Y-->>Y: top_level_modules = PurePosixPath(file).parts[0] per distinct file, deduped + sorted -- directory heuristic, not real package resolution
        Y->>P: explain_repository_summary(evidence) -- same NVIDIA -> Groq -> OpenRouter -> Gemini -> Local chain as ask/analyze_impact
        P-->>Y: extract-JSON-then-validate loop (generation.pipeline's pattern, duplicated locally)
        Y-->>Y: reject any referenced_modules entry absent from real top_level_modules -- retry, same as a JSON-validation failure
        alt every provider fails or none configured
            Y-->>M: explanation=None -- deterministic evidence still returned (mirrors analyze_impact's D-024 degradation)
        else a provider succeeds
            Y-->>M: verified narrative string
        end
    end
    M-->>U: RepositorySummaryResult
```

Same lock discipline as `analyze_impact`: only the `.chunks` read happens
under `state.lock`, released before deterministic aggregation and any LLM
call. See DECISIONS.md D-025 for the full design rationale, including why
`build_repository_summary` needed no `mcp/server.py` import alias (unlike
`analyze_impact`'s `as compute_impact`).

---

## 4. Provider selection

This section has two distinct mechanisms, kept deliberately separate (see
DECISIONS.md D-022): **config-time precedence** (which providers are even
candidates, decided once per call from `.env`) and **runtime
fallback-on-failure** (which candidate's failure moves to the next,
decided while `generate_answer` is actually running). Confusing the two
would hide the difference between "nothing was ever configured"
(`NoProviderConfiguredError`) and "candidates existed but every one failed"
(`AllProvidersFailedError`).

**Config-time precedence** — `generation.providers.registry.select_providers`:

```mermaid
flowchart TD
    Cfg[Read .env] --> K1{NVIDIA_API_KEY set?}
    K1 -- yes --> P1[NVIDIA adapter]
    K1 -- no --> K2{GROQ_API_KEY set?}
    P1 --> K2
    K2 -- yes --> P2[Groq adapter]
    K2 -- no --> K3{OPENROUTER_API_KEY set?}
    P2 --> K3
    K3 -- yes --> P3[OpenRouter adapter]
    K3 -- no --> K4{GEMINI_API_KEY set?}
    P3 --> K4
    K4 -- yes --> P4[Gemini adapter]
    K4 -- no --> K5{LOCAL_MODEL_NAME set?}
    P4 --> K5
    K5 -- yes --> P5[Local OpenAI-compatible adapter]
    K5 -- no --> Done[Return configured list, in this order]
    P5 --> Done
    Done -- list is empty --> X[NoProviderConfiguredError]
```

The precedence above keeps the most-specific provider keys first so an
accidentally-populated local block does not silently override a paid
provider. This selection is purely additive/ordering — it never inspects
whether a provider will actually succeed.

**Runtime fallback-on-failure** — `generation.pipeline.generate_answer`,
consuming the ordered list above:

```mermaid
flowchart TD
    Start[providers = select_providers list] --> Next{Any providers left to try?}
    Next -- no --> Fail[AllProvidersFailedError]
    Next -- yes --> Try[Call next provider.complete]
    Try -- ProviderRequestError incl. timeout --> Next
    Try -- raw text --> Extract[_extract_json_object then validate]
    Extract -- invalid, retries remain --> Retry[Re-prompt same provider]
    Retry --> Try
    Extract -- invalid, retries exhausted --> Next
    Extract -- valid --> Success[Use this provider's answer]
```

A provider is retried in place (same provider, corrected prompt) up to
`GENERATION_JSON_RETRY_LIMIT` times for a JSON-validation failure only —
a `ProviderRequestError` is never retried, it moves straight to the next
configured provider.

---

## 5. Data lifecycle

| Stage         | Lives where                            | Owner            |
| ------------- | -------------------------------------- | ---------------- |
| Raw source    | User's filesystem                      | User             |
| Parsed AST    | Memory only during ingest              | `parser/`        |
| Chunks        | Memory only (aggregated by `indexing.repo.collect_repo_chunks`) | `chunker/`, `indexing/repo.py` |
| Vector index  | `INDEX_DIR/vector.faiss` + `INDEX_DIR/vector_metadata.json` | `indexing/vector.py` |
| BM25 index    | `INDEX_DIR/bm25.pkl` + `INDEX_DIR/bm25_metadata.json` | `indexing/bm25.py`   |
| Reference index | `INDEX_DIR/references.json` (plain JSON, absence is lenient) | `indexing/references.py` |
| Chunk cache   | `INDEX_DIR/chunk_cache.json` (plain JSON, keyed by file+content hash, absence is lenient -- Day 11) | `indexing/cache.py` |
| Manifest      | `INDEX_DIR/manifest.json` (repo_root, source)          | `indexing/manifest.py` |
| Logs          | stderr / `LOG_LEVEL`                   | `cli`, `mcp`     |
| Caches        | `.cache/`, `.mypy_cache/`, `.ruff_cache/` (gitignored) | tooling  |

Anything in `data/` or matching `*.faiss` / `*.index` is gitignored —
indexes are reproducible from source and should not be committed.
