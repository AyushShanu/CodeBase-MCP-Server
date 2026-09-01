# Spec: V2 Polish & Evaluation

## Overview
Day 10 shipped `analyze_impact` and the reference/symbol layer, closing the
last purely-new-tool gap in CLAUDE.md's V2 tool table. This day does not add
a new pipeline stage — it closes the remaining named V1/V2 gaps that earlier
specs deliberately deferred with "Day 11 scope" language: `repository_summary`
(the last unbuilt MCP tool), sharper impact-analysis prompts/citations,
caching/incremental indexing so `codebase-rag index` is usable on a repo more
than once, running the Day 6 benchmark set for real and fixing what it finds,
security controls named but not fully built (path restriction, secret-file
exclusion, prompt-injection-aware handling), and CLI polish. It belongs here
because every deterministic-evidence building block (`indexing/`, `impact/`,
`generation/`, `mcp/server.py`) already exists; this day tightens and
evaluates what's there rather than adding new retrieval/generation logic.

**Phasing.** This day bundles six genuinely independent concerns, unlike
every prior day's single-feature scope. To keep that honest rather than
silently over-scoping, implement and merge in this order, each independently
demoable before starting the next — do not half-build two phases at once
(same rule CLAUDE.md already states for the top-level roadmap):

1. `repository_summary` (new tool, no dependencies on the rest of this spec).
2. Secret-file exclusion (`ingestion/filters.py`) — small, isolated.
3. Path-restriction re-verification (`get_file_context`) — small, isolated;
   only makes code changes if a real gap is actually found.
4. Prompt-injection-aware wording + `impact/explain.py`/`ask` prompt
   tightening — isolated, reuses existing citation-verification unchanged.
5. Incremental indexing (`indexing/cache.py`, `indexing/repo.py`,
   `indexing/manifest.py`, `cli/main.py`) — by far the largest and riskiest
   phase; see its own section below for the concrete mechanism, which was
   the one part of this spec's first draft that described a *decision*
   ("skip unchanged files") without describing a *mechanism*.
6. Benchmark run + triage. Deliberately last: it exercises everything built
   in phases 1–5, and its own "fix what it finds" scope is open-ended by
   nature (see its Rules entry below for the explicit boundary on that).

If phase 5 alone grows beyond what's comfortable for one day, that is a
legitimate reason to split it into its own day/branch rather than compress
it — flag that to the user rather than cutting corners on the mechanism.

**Precondition, all phases:** confirm `data/clones/` (or `data/` generally)
is actually excluded in `ingestion/filters.py` before starting phase 5. This
was identified as a real gap earlier in the project (stray cloned demo repos
polluting an index because `data/` wasn't ignored) and was meant to be fixed
then. If it wasn't, fix it as part of phase 2 (same file, same kind of
change as secret-file exclusion) — incremental indexing makes this bug
*stickier*, not less relevant: today a full reindex is what surfaces and
fixes accidental pollution; with a persistent per-file cache, a polluted
file just gets marked "unchanged" and silently persists across every future
incremental run instead of being caught the next time someone reindexes.

## Depends on
- Day 08 (`feature/mcp-server-v1`) — MCP server, `get_file_context`'s
  resolve-then-`relative_to` containment check (D-023), which explicitly
  named "full path restriction hardening" as Day 11 scope.
- Day 07 (`feature/llm-generation-citations`) — provider fallback chain,
  citation verification (D-022), which named prompt-injection hardening as
  "one cheap mitigation now, full hardening later."
- Day 10 (`feature/symbol-impact-analysis`) — reference index, `analyze_impact`,
  `impact/symbols.py` matching (specifically `strip_part_suffix` and
  `count_distinct_definitions`, both reused as-is by `repository_summary` —
  see below), `impact/explain.py` narrative generation (D-024) —
  `repository_summary` and "improved impact-analysis prompts/citations"
  build directly on these.
- Day 02 (`feature/github-ingestion`) — `ingestion/filters.py`'s
  `classify_file`/`IGNORED_DIR_NAMES`, which secret-file exclusion extends,
  and which the `data/clones/` precondition above also lives in.
- Day 04/05/06 (embeddings, hybrid retrieval, reranker) — `indexing/vector.py`'s
  existing separation of `embed_chunks` (the expensive step) from the
  FAISS-structure-building step is exactly what incremental indexing's
  caching mechanism keys off; no change to the embedding/reranker *models*
  is in scope here, only how their outputs are cached and reused.

## Pipeline stage(s) touched
MCP Server (new `repository_summary` tool) · Impact Analysis (prompt/citation
refinement only, not new evidence types) · Ingestion (secret-file exclusion
rule, `data/clones/` exclusion confirmation) · Generation (prompt-injection-
aware instruction wording, reused verification mechanism) · Indexing
(per-file caching + incremental rebuild in `indexing/repo.py`/new
`indexing/cache.py` + CLI). No changes to Parsing & Chunking, Embedding
model, BM25 scoring algorithm, Hybrid Retrieval, or Reranking themselves —
only how already-computed embeddings get reused across runs.

## MCP tools affected
- `repository_summary` — **new**. Summarizes repo structure (languages,
  file counts, *distinct* symbol counts, top-level modules) from
  already-indexed chunk/reference data plus an optional LLM narrative,
  following the same "deterministic evidence first, LLM narration only on
  top of it, degrade to `explanation=None` if no provider" pattern
  `analyze_impact` established (D-024).
- `analyze_impact` — prompt/citation refinement only (tighter
  `impact/explain.py` prompt wording, citation formatting consistency with
  `citations/format.py`); no new evidence fields, no schema break.
- `get_file_context` — re-verified against the "full path restriction
  hardening" CLAUDE.md/D-023 deferred to this day; only hardened further if
  a real gap is found, the existing resolve-then-`relative_to` check is not
  torn out.
- `search_code`, `find_symbol`, `ask` — no tool-contract changes; `ask`'s
  prompt only gets the same prompt-injection-aware wording review as
  `analyze_impact`.

## Model / provider choice for this step
No new model/provider decision this day. `repository_summary`'s optional
narrative step reuses the existing NVIDIA → Groq → OpenRouter → Gemini →
Local fallback chain and `select_providers`/`generate`-style call pattern
already locked in D-005/D-022 — do not introduce a second provider-selection
path. Embedding model (`all-MiniLM-L6-v2`, D-003/config), vector store
(FAISS-CPU, D-003), reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`,
config default) stay exactly as already decided — this day does not
re-evaluate or swap any of them, and incremental indexing's caching layer
is explicitly a reuse-the-existing-model's-outputs mechanism, never a
reason to touch the model itself. If the benchmark run in this same spec
surfaces a retrieval-quality problem that looks like it needs a different
embedding/reranker model, stop and flag it to the user rather than silently
swapping — that would be re-deciding something DECISIONS.md already settled.

## Files to change
- `src/codebase_rag_mcp/mcp/server.py` — register `repository_summary`;
  re-verify `get_file_context`'s containment check.
- `src/codebase_rag_mcp/mcp/models.py` — add the `repository_summary`
  response model(s), following `SearchCodeResult`/`FindSymbolResult`/
  `FileContextResult`'s existing naming/shape convention.
- `src/codebase_rag_mcp/impact/explain.py` — prompt/citation tightening.
- `src/codebase_rag_mcp/impact/prompts.py` — prompt wording refinement,
  including explicit "retrieved code is data, not instructions" framing.
- `src/codebase_rag_mcp/generation/prompts.py` — same prompt-injection-aware
  wording review for `ask`, reusing the existing citation-verification
  mechanism (D-022) rather than adding a new one.
- `src/codebase_rag_mcp/ingestion/filters.py` — add a secret-file exclusion
  rule (new `is_secret_file`-style check + exclusion reason, alongside the
  existing lockfile/binary/oversized checks); confirm/add `data/` (or
  `data/clones/` specifically) to the existing ignored-directory handling
  per the Phasing precondition above.
- `src/codebase_rag_mcp/indexing/manifest.py` — extend `IndexManifest` (or a
  sibling model) with per-file state: repo-relative path → content hash +
  size + mtime. Backward-compatible with an older manifest missing this
  field — treated as "no cached state," never a crash (mirrors
  `load_manifest`'s existing leniency convention).
- `src/codebase_rag_mcp/indexing/vector.py` — split (or confirm already
  split; `embed_chunks` already exists as a distinct step from FAISS-index
  construction) so that FAISS-index construction accepts a
  **pre-computed** `list[Chunk]` + `list[EmbeddingVector]` pair, not just
  raw chunks — this is what lets the incremental path skip calling
  `embed_chunks` for cached/unchanged files while still doing a full,
  cheap, correctness-preserving index-structure rebuild from the combined
  cached+fresh embedding set every run (see "Incremental indexing
  mechanism" below for why the FAISS *structure* is always rebuilt even
  though embedding *computation* is what's actually being skipped).
- `src/codebase_rag_mcp/indexing/repo.py` — `collect_repo_chunks` (or a new
  wrapper around it) consults `indexing.cache.ChunkCache` per file: reuse
  cached chunks/embeddings/references when the file's current content hash
  matches the cache; otherwise parse/chunk/embed fresh and update the
  cache entry. `build_all_indexes` always assembles the *complete* current
  chunk/embedding/reference set (cached ∪ fresh) and always fully rebuilds
  the FAISS structure, BM25 index, and reference index from that complete
  set every run — never a partial/in-place patch of any of the three (see
  below for why).
- `src/codebase_rag_mcp/cli/main.py` — `index` subcommand gains a
  `--force`-style full-rebuild flag (bypasses the cache entirely) and
  prints skipped-vs-reparsed-vs-reembedded-vs-deleted counts; `serve`'s
  help text already lists all five V1 tools plus `analyze_impact`, update
  it to also list `repository_summary`.
- `CLAUDE.md` — flip Day 10's and this day's roadmap checkbox once actually
  done (Day 10 is functionally complete per git history but still shows
  `[ ]` — flag this mismatch to the user before touching it, don't silently
  "fix" it as part of this spec).

## Incremental indexing mechanism (concrete design — replaces the first
## draft's under-specified "skips re-parsing/re-embedding")
The expensive, worth-caching steps are Tree-sitter parsing/chunking and
embedding-model inference (`embed_chunks`) — both pure functions of a
file's content. BM25 and the reference index are cheap, CPU-only,
deterministic aggregations with **no external model call**, and BM25 in
particular has a correctness reason to never be patched incrementally: its
IDF weighting is a function of the *entire* corpus, so patching just the
changed documents' term counts would silently drift every other document's
score. So the design is: **cache the expensive per-file outputs, but always
fully rebuild all three persisted structures (FAISS, BM25, references) from
the complete current set of chunks/embeddings/references on every run.**
That "full rebuild" is cheap — it's re-deriving a data structure from
already-computed inputs, not re-running the embedding model or re-parsing
source.

- `src/codebase_rag_mcp/indexing/cache.py` (**new file**) —
  `ChunkCacheEntry` (Pydantic, frozen): `file`, `content_hash` (stdlib
  `hashlib.sha256` over the file's bytes), `chunks: list[Chunk]`,
  `embeddings: list[EmbeddingVector]` (one per chunk, same order — stored
  as plain JSON float lists, independent of `vector.faiss`'s own on-disk
  representation, so this cache never depends on a specific FAISS index
  type supporting vector reconstruction), `references: list[RawReference]`.
  `ChunkCache.load(index_dir)` / `.write(index_dir)` persist as
  `chunk_cache.json` (plain JSON, same no-pickle-needed precedent
  `indexing/references.py` already set — a missing file is "empty cache",
  never a crash). `ChunkCache.get(file, content_hash) -> ChunkCacheEntry
  | None` returns `None` on any miss (unknown file, or a hash mismatch —
  i.e. the file changed). `ChunkCache.put(entry)` upserts.
- Per-file logic in `collect_repo_chunks`'s loop: compute
  `content_hash`; `cache.get(file, content_hash)` hit → reuse its
  `chunks`/`embeddings`/`references` directly, skip `parser.parse_file`,
  `chunker.chunk_file`, and `vector.embed_chunks` entirely for that file;
  miss → run the existing full pipeline for that file, then
  `cache.put(...)` the fresh result.
- **Deletion/rename handling:** after processing every currently-ingested
  file, drop any cache entries whose `file` is not among the files just
  processed, *before* writing the cache back out. This is what keeps a
  deleted or renamed file's stale chunks/embeddings/references from
  persisting forever — the single biggest gap in the first draft, which
  only described the unchanged-file case and never addressed removal.
- `build_all_indexes` combines every currently-valid cache entry (after the
  drop step above) plus every freshly-computed one into one complete
  `list[Chunk]`/`list[EmbeddingVector]`/`list[RawReference]`, and always
  calls the full `vector` FAISS-structure-build, `bm25.build_index`, and
  `references.build_index` over that complete set — every run, incremental
  or not. What "incremental" actually buys you is skipping
  parse/chunk/`embed_chunks` for unchanged files, not skipping index
  construction itself.
- `--force` bypasses `ChunkCache` entirely (treats every file as a cache
  miss) without deleting the cache file, so a forced full rebuild also
  naturally repopulates/corrects the cache for the next incremental run.

## Files to create
- `src/codebase_rag_mcp/indexing/cache.py` — see "Incremental indexing
  mechanism" above.
- `src/codebase_rag_mcp/impact/summary.py` — deterministic repo-structure
  aggregation reading the already-built vector/BM25/reference indexes'
  chunk metadata; no new index format. **Symbol counts must reuse
  `impact.symbols.strip_part_suffix`/`count_distinct_definitions`** rather
  than counting chunks directly — Day 10 built that helper specifically
  because a naive per-chunk count overcounts every oversized symbol split
  into `#part1`/`#part2` by the chunker's fallback path (see D-024);
  `repository_summary` reusing chunk-level counts instead would silently
  reintroduce the exact overcounting bug that helper exists to prevent,
  in a tool whose entire purpose is to state repo-level numbers people
  will trust at a glance. **"Top-level modules" is defined precisely as:**
  for each included file, take the first path segment after the configured
  language root (e.g. `src/codebase_rag_mcp/<first-segment>` for this
  project's own layout) with no trailing file extension; deduplicate;
  report the count and the list. This is a directory-structure heuristic,
  not real package-boundary resolution (no `__init__.py`/`package.json`
  parsing) — an explicitly accepted simplification, consistent with this
  project's existing name-based-not-fully-resolved conventions elsewhere,
  and should be stated as such in the tool's own docstring so its output
  isn't read as more precise than it is.
- `benchmarks/run_benchmark.py` — loads `benchmarks/questions.json` (Day 6's
  20-question set, **extended by this day** — see Rules — with a small
  number of new questions specifically exercising `analyze_impact` and
  `repository_summary`, since the existing 20 predate both tools and
  otherwise leave them with unit-test coverage but zero end-to-end
  regression coverage going forward), runs each question through
  `search_code`/`ask`/`analyze_impact`/`repository_summary` as appropriate
  against a real indexed repo, scores hits against
  `expected_file`/`expected_symbol` per `category`
  (`exact_symbol`/`semantic`/`structural`, plus a new `impact` category for
  the added questions), and writes a report.
- `benchmarks/results/.gitkeep` (or committed sample report) — output
  location for benchmark run reports.
- `tests/test_repository_summary.py`
- `tests/test_indexing_cache.py` — `ChunkCache` load/save/get/put
  round-trip; a hash-mismatch correctly reports a miss; an entry for a file
  no longer on disk is correctly dropped after a `collect_repo_chunks` run
  (the deletion case).
- `tests/test_indexing_incremental.py` — end-to-end: index a small fixture
  repo, assert cache populated; reindex unchanged, assert
  parse/chunk/`embed_chunks` were **not** called for any file (via a
  spy/mock, not just a skip-count log) and the resulting FAISS/BM25/
  reference indexes are byte-for-byte/behaviorally identical to the first
  run's; modify one file, reindex, assert only that file was
  reparsed/reembedded and a known query's top results are unchanged for
  content that didn't change; delete a file, reindex, assert its chunks no
  longer appear in any of the three persisted indexes; `--force` bypasses
  the cache regardless of hash matches.
- Extend (not necessarily new files) `tests/test_ingestion.py` for the
  secret-file exclusion rule and the `data/clones/` exclusion confirmation,
  and `tests/test_mcp_server.py` for `repository_summary`'s tool wiring.

## New dependencies
No new dependencies. Incremental indexing uses stdlib `hashlib` for content
hashing and plain JSON for the chunk/embedding cache (no pickle); the
benchmark runner uses the already-present `json` stdlib module and the
existing pipeline/MCP-adjacent call path — no new pip package, no new API
key.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code (unchanged this day; no chunking logic is touched).
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata.
- LLM answers must rely only on retrieved evidence; `repository_summary`'s
  optional narrative follows `analyze_impact`'s existing "verified narrative
  or `explanation=None`" pattern (D-024) — never a narrative built from a
  fabricated file/module reference.
- All LLM calls go through the provider fallback chain defined in CLAUDE.md
  — never hardcode a single provider, and never add a second
  provider-selection mechanism alongside `generation.providers.registry
  .select_providers`.
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose, matching `mcp/models.py`'s existing model shapes.
- Embedding and reranker models run locally by default — no paid API in the
  default path; this day does not change either model, only how their
  outputs are cached and reused across index runs.
- Do not silently re-decide the vector store, embedding model, or reranker
  choices already logged in DECISIONS.md D-003 and `config.py` defaults —
  if the benchmark run suggests one of them is the bottleneck, flag it to
  the user instead of swapping it. **This applies with equal force to the
  benchmark-triage item below: a failure that traces to existing glue-code
  logic (a wrong file filter, an off-by-one in citation formatting, a
  prompt wording issue) is fixed in this day; a failure that appears to
  require an algorithm- or model-level change (retrieval ranking quality,
  chunking strategy) is flagged to the user and recorded as an accepted V2
  gap, never open-endedly iterated on inside this day's scope.**
- Incremental indexing must degrade safely: a manifest or chunk cache from
  an older index (missing the new per-file hash data, or simply absent) is
  treated as "no cached state" — every file is a cache miss, a normal full
  index run results, never a crash. BM25 and the reference index are
  **always** fully rebuilt from the complete current chunk/reference set
  on every run, incremental or not — never patched in place — because
  BM25's IDF weighting is a whole-corpus quantity that a partial update
  would silently corrupt. A cache entry for a file no longer present in
  the current ingestion pass is dropped before the cache is persisted,
  every run — this is what makes deletions/renames actually take effect
  instead of leaving ghost entries.
- Secret-file exclusion is a **filename/path-pattern check only, not
  content scanning** — state this explicitly in the code comment,
  `DECISIONS.md`, and the tool-facing documentation, the same way
  `analyze_impact`'s name-based-matching limitation is stated explicitly
  rather than left implicit. A credential hardcoded inside an
  ordinarily-named file (e.g. a stray API key in `settings.py`) is not
  caught by this control, and nothing should imply otherwise.
- Secret-file exclusion extends `ingestion/filters.py`'s existing
  ordered-check pattern (`classify_file`) and reports a distinct exclusion
  reason (e.g. `"secret_file"`), it does not replace the lockfile/binary/
  oversized checks.
- Path-restriction re-verification reuses `get_file_context`'s existing
  resolve-then-`relative_to` discipline (D-023) rather than introducing a
  second containment mechanism.
- Prompt-injection-aware wording is a prompt-level mitigation plus the
  already-existing mechanical citation verification (D-022/D-024's
  reject-fabricated-references retry) — do not claim to fully "solve"
  prompt injection; state explicitly in DECISIONS.md what is and isn't
  covered, same as D-022 already does.
- Before running any benchmark or `repository_summary` check against "the
  indexed p-queue demo repo," **explicitly confirm that index was built
  fresh by this day's own (possibly-incremental) indexing code and
  includes a current `references.json`** — do not assume a previously
  built index is trustworthy. This project has independently hit a
  wrong/stale/incomplete index at least three separate times before this
  day (a leftover clone polluting the corpus, a live server pointed at a
  different repo's index, a `references.json` missing after Day 10 shipped
  because the default index predated it); treat that as a pattern to
  guard against by habit, not a one-off.
- Log every meaningful decision to `DECISIONS.md`, including the benchmark
  results summary and which failures were fixed vs. accepted as V2 gaps,
  and the incremental-indexing cache design (why BM25/references are
  always fully rebuilt while only embedding computation is cached, and the
  deletion-handling rule).
- Update `FLOW.md` with the `repository_summary` tool's flow (mirroring the
  existing `analyze_impact` sequence diagram) and the incremental-indexing
  path in the offline build flow section, including the cache
  hit/miss/deletion branches.

## Definition of done
- [ ] `repository_summary` is registered as an MCP tool, returns real
      structural data (language/file counts, **distinct** symbol counts via
      `impact.symbols.count_distinct_definitions`, top-level modules per
      the precise definition above) when run against a **freshly rebuilt**
      indexed `p-queue` demo repo (confirmed to include a current
      `references.json`), and degrades to `explanation=None` (not an
      error) when no LLM provider is configured.
- [ ] `tests/test_repository_summary.py` passes, including a
      zero-index-data edge case and an oversized/split-symbol case
      confirming symbol counts are not inflated by `#partN` chunks.
- [ ] `tests/test_indexing_cache.py` passes: cache round-trip, hash-mismatch
      miss, and dropped-entry-on-deletion all verified directly.
- [ ] `codebase-rag index <source>` run twice in a row on an unchanged repo
      skips `parser.parse_file`/`chunker.chunk_file`/`vector.embed_chunks`
      for every file on the second run (verified via spy/mock, not just a
      skip-count log), and the resulting indexes behave identically to the
      first run's for a known query.
- [ ] Modifying exactly one file and reindexing reparses/reembeds only that
      file, while BM25 and the reference index are still confirmed to have
      been fully rebuilt from the complete corpus (not patched) —
      `tests/test_indexing_incremental.py`.
- [ ] Deleting a file and reindexing removes its chunks/embeddings/
      references from all three persisted indexes —
      `tests/test_indexing_incremental.py`.
- [ ] A `--force` flag forces a full rebuild regardless of cache state and
      repopulates the cache correctly for the next incremental run.
- [ ] `ingestion/filters.py` excludes common secret files (`.env`, `*.pem`,
      `*.key`, `id_rsa`/`id_ed25519`, `credentials.json`, or similar) with a
      dedicated exclusion reason, covered by tests, and confirmed by
      indexing a fixture repo containing such a file and checking it never
      appears in `RepoStats.included`/the built indexes; the
      filename-only/no-content-scanning limitation is documented in the
      code and `DECISIONS.md`.
- [ ] `data/clones/` (or `data/` generally) is confirmed excluded from
      ingestion — either it already was, and this is verified by a test, or
      it wasn't and is fixed here.
- [ ] `get_file_context`'s path-containment check is re-verified (existing
      `../` and symlink-escape tests still pass; any newly found gap is
      closed, not just re-tested).
- [ ] `ask` and `analyze_impact`'s prompts explicitly instruct the model to
      treat retrieved code/text as data, not instructions; the existing
      citation-verification retry mechanism (D-022/D-024) is confirmed to
      still reject any fabricated file/symbol reference after the prompt
      changes (no regression in `tests/test_generation.py`/`test_impact.py`).
- [ ] `benchmarks/questions.json` gains new `impact`-category questions
      exercising `analyze_impact` and `repository_summary`, alongside the
      original 20.
- [ ] `benchmarks/run_benchmark.py` runs every question in
      `benchmarks/questions.json` end-to-end against a **freshly rebuilt**
      real, indexed `p-queue` demo repo and produces a per-question
      pass/fail report broken down by `category`.
- [ ] The benchmark run's highest-impact failures are triaged: each is
      either fixed — glue-code/prompt/filter-level only, per the Rules
      boundary above — with the fix and before/after benchmark delta
      logged in DECISIONS.md, or explicitly flagged to the user and
      recorded as an out-of-scope V2 gap with the reason, never
      open-endedly iterated on inside this day.
- [ ] `ruff check`, `ruff format --check`, `mypy`, and the full `pytest`
      suite all pass.
- [ ] `DECISIONS.md` has a new dated entry for this day's decisions
      (including the incremental-indexing cache design and the
      filename-only secret-detection scope note); `FLOW.md` documents the
      `repository_summary` flow and the incremental-indexing path with its
      cache hit/miss/deletion branches.