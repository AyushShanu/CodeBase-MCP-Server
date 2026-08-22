# Spec: BM25 & Hybrid Retrieval

## Overview
Day 04 (embeddings-vector-index) gave the pipeline its first queryable index —
a FAISS `IndexFlatIP` over L2-normalized chunk embeddings (`indexing/vector.py`,
`VectorIndex.query`) — but dense-only search is weak on exact-identifier
lookups (a query like `generateToken` should surface the literal function by
name, not by loose semantic similarity). D-004 already accepted a hybrid
dense+sparse design and explicitly deferred the merge strategy to "when
implementing `retrieval/`" — this is that day. It has two parts: first, wire
`rank-bm25` into `indexing/bm25.py` (currently a docstring-only placeholder
per D-007) with the same build/persist/load/query lifecycle shape Day 04
established for the vector index; second, implement `retrieval/hybrid.py`
(currently a placeholder) to run both indexes over the same query and merge
their candidate lists into one transparently-scored, ranked result. This is
the last stage before reranking (Day 06) and generation (Day 07) — everything
downstream consumes `hybrid_search`'s output, so its scoring must be
explainable, not a black box, and its behavior must be verified against a
real repository, not just fixtures — this is the day two independently
correct subsystems get merged, which is exactly where "each half passes its
own tests" and "the combination behaves sensibly" can diverge.

## Depends on
- Day 01 — Foundation (package scaffold, config, CLI, MCP stub). Complete.
- Day 02 — GitHub Ingestion & File Filtering. Complete.
- Day 03 — Tree-sitter Parsing & AST Chunking (`chunker.models.Chunk`).
  Complete.
- Day 04 — Embeddings & Vector Index (`indexing.vector.VectorIndex`,
  `indexing.vector.load_index`, `indexing.models.VectorQueryResult`,
  `indexing.repo.collect_repo_chunks`). Complete per
  `.claude/specs/04-embeddings-vector-index.md`. This day's BM25 index is
  built over the exact same `list[Chunk]` Day 04's vector index is built
  over (same repo-wide `collect_repo_chunks` output) — the two indexes must
  stay in sync (same chunk set, same `Chunk.id` values) so a hybrid result
  can merge them by chunk identity.

## Pipeline stage(s) touched
- **BM25** (new logic — `indexing/bm25.py` is currently a placeholder).
- **Hybrid Retrieval** (new logic — `retrieval/__init__.py` and the new
  `retrieval/hybrid.py` are currently placeholders).

No other stage (Ingestion, Parsing & Chunking, Embedding, Reranking,
Generation, Citations, MCP Server, Impact Analysis) is touched. The FAISS
vector index and `VectorIndex`/`embed_texts` built in Day 04 are consumed
as-is, not modified.

## MCP tools affected
No MCP tool changes. `search_code` lands in Day 08 and will call this day's
`hybrid_search` internally, but no MCP-facing code exists yet.

## Model / provider choice for this step
This day touches the **Keyword search** row of CLAUDE.md's stack table.
CLAUDE.md lists only one option here — `rank-bm25`, no alternatives — so
there is no provider choice to make; `rank-bm25>=0.2.2` is already declared
in `pyproject.toml` (added at Day 01 scaffolding).

**The merge strategy is the one open decision this day must make and log** —
D-004 explicitly left it unresolved ("need a merge strategy (RRF, weighted
sum, ...); to be decided when implementing `retrieval/`"), so per this
project's workflow it must be named and reasoned about, not silently picked:

- **Chosen: Reciprocal Rank Fusion (RRF).** For each candidate chunk,
  `score = sum(1 / (k + rank))` over whichever of {BM25 results, vector
  results} it appears in. `k = 60` (the conventional default from the
  original RRF paper). **`rank` is 1-indexed** — the top result in a list
  has `rank = 1`, not `0`. This must be a literal, stated convention in the
  code (a comment at the fusion function, not just "whatever `enumerate()`
  happens to produce") since it is exactly the kind of off-by-one that
  changes every score slightly without crashing anything or obviously
  failing a loose test.
  Rationale: BM25 scores are unbounded and corpus-dependent while vector
  scores are bounded cosine similarities in `[-1, 1]` — combining them with
  a weighted sum requires normalizing two incomparable scales (and
  re-tuning the weight per corpus), whereas RRF only needs each list's
  *rank order*, which is already well-defined on both sides. This also
  matches how CLAUDE.md's own Day 05 line lists "reciprocal rank" as the
  first-mentioned scoring option.
- **Alternative (documented, not built): weighted-sum of normalized
  scores** (e.g. min-max or z-score normalize each list's scores to
  `[0, 1]`, then `score = α * vector_score + (1 - α) * bm25_score`). More
  tunable (the `α` weight is an explicit knob) but adds a free parameter
  that needs benchmarking to set sensibly, and normalization choice itself
  is another decision. Documented here as the alternative CLAUDE.md's
  "transparent scoring/reciprocal-rank" phrasing also allows for.

Log the RRF choice, `k = 60`, and the 1-indexed rank convention to
`DECISIONS.md` — this is exactly the kind of already-flagged-as-open
decision CLAUDE.md's workflow says must not be picked silently.

**Tokenization scheme — also decided here, not left open**, since the
spec's own exact-symbol Definition-of-Done item depends on it being
concrete: lowercase the text, then split on any run of non-alphanumeric
characters (`re.split(r"[^a-zA-Z0-9]+", text)` or equivalent) — this turns
`generateToken()` into tokens `["generatetoken"]` for the identifier itself
plus separately-split surrounding punctuation/whitespace. **CamelCase and
snake_case are *not* split into sub-words in this first version** (i.e.
`generateToken` stays one token, it is not additionally split into
`generate` + `token`) — this keeps whole-identifier queries (CLAUDE.md's own
"a query like `generateToken`" example) working correctly with zero
ambiguity, at the cost of a space-separated query like `"generate token"`
not matching `generateToken` via BM25 (it would still be reachable via the
vector side). Document this as an explicit, named limitation in
`DECISIONS.md` — camelCase/snake_case sub-word splitting is a reasonable
future improvement (Day 09/11 polish), not silently out of scope.

## Files to change
- `src/codebase_rag_mcp/indexing/bm25.py` — replace the placeholder
  docstring-only module with the real BM25 build/persist/load/query
  implementation, mirroring `indexing/vector.py`'s shape (`build_index` /
  `load_index` / a queryable class / typed stats and query-result models).
- `src/codebase_rag_mcp/indexing/models.py` — add BM25-side models
  following the existing `VectorIndexStats`/`VectorQueryResult` pattern
  (e.g. `Bm25IndexStats`, `Bm25QueryResult`).
- `src/codebase_rag_mcp/indexing/exceptions.py` — add BM25-specific
  exceptions subclassing `IndexingError`, mirroring
  `IndexNotBuiltError`/`IndexLoadError`/`EmptyIndexError`
  (e.g. `Bm25NotBuiltError`, `Bm25LoadError`, `EmptyBm25IndexError`).
- `src/codebase_rag_mcp/indexing/repo.py` — add (or confirm, if already
  shaped this way from Day 04) a single orchestration entry point — e.g.
  `build_all_indexes(root: Path) -> tuple[VectorIndexStats, Bm25IndexStats]`
  — that calls `collect_repo_chunks` **once** and builds both the vector
  index and the BM25 index from that one `list[Chunk]`, rather than each
  index having its own independent call to `collect_repo_chunks`. This is
  what actually enforces the "same chunk set, same `Chunk.id` values"
  invariant this spec requires elsewhere — two separate build calls against
  a repo that could change between them (e.g. a local path being edited)
  would otherwise be able to silently desync the two indexes despite both
  individually working correctly. `cli/main.py` wiring to trigger this from
  the command line is not required this day (no MCP/CLI-facing day yet),
  but the two indexes must never be built via two independent
  `collect_repo_chunks()` calls, even in tests or the manual smoke check
  below.
- `src/codebase_rag_mcp/retrieval/__init__.py` — replace the placeholder
  with real exports (`hybrid_search`, `HybridQueryResult`, or equivalent
  names chosen during implementation).
- `src/codebase_rag_mcp/config.py` — add the merge-strategy constants
  (`RRF_K` default `60`) and a `HYBRID_CANDIDATE_POOL_SIZE` (how many
  results to pull from each of BM25/vector before merging — this is
  deliberately larger than the final `top_k` returned to the caller, since
  Day 06's reranker needs a wider candidate pool to work with), alongside
  the existing `INDEX_DIR`/`EMBEDDING_MODEL_NAME` settings.
- `.env.example` — document the new hybrid-retrieval settings under a
  "Retrieval settings" section, defaulted and commented like the existing
  entries.
- `FLOW.md` — fill in the currently-placeholder `F` box (BM25 index) in the
  "Offline build flow" diagram, and add a real hybrid-query flow (currently
  the Section 3 sequence diagram shows `R-->>R: ANN over FAISS + BM25` as a
  single unlabeled step — expand it to show the actual merge).

## Files to create
- `src/codebase_rag_mcp/retrieval/hybrid.py` — the hybrid search entry
  point: given a query string, run it through both
  `indexing.bm25`'s query and `indexing.vector.VectorIndex.query`, merge the
  two candidate lists via RRF (`k = 60`, 1-indexed rank — see above), and
  return one ranked `list[HybridQueryResult]` with each result's
  contributing sub-scores visible (not just the final merged score) —
  CLAUDE.md calls for "transparent scoring," which means a caller/test can
  see *why* a chunk ranked where it did, not just the final number.
- `src/codebase_rag_mcp/retrieval/models.py` — Pydantic models for this
  stage's output, matching the `indexing.models`/`chunker.models`
  convention: `HybridQueryResult` (chunk, merged score, and the BM25
  rank/score and vector rank/score it came from, where applicable — a chunk
  found by only one side has the other side's contribution as `None`/absent,
  not a fabricated zero).
- `src/codebase_rag_mcp/retrieval/exceptions.py` — module-specific
  exceptions matching the `indexing.exceptions` convention (e.g.
  `RetrievalError` base, `NoIndexAvailableError` for querying before either
  index is built/loaded).
- `tests/test_indexing_bm25.py` — unit tests for BM25 build → persist →
  load → query, using a small in-memory `Chunk` fixture set (no real GitHub
  clone needed), mirroring `tests/test_indexing_vector.py`'s structure.
- `tests/test_retrieval_hybrid.py` — unit tests for the merge logic itself,
  including CLAUDE.md's explicit Day 05 requirement to "test semantic vs.
  exact-symbol queries separately": one test where an exact function/symbol
  name query should be found via BM25 strength, one test where a
  natural-language/semantic query should be found via vector strength, and
  one test confirming a chunk found by both indexes outranks one found by
  only one.

## New dependencies
No new dependencies. `rank-bm25>=0.2.2` is already declared in
`pyproject.toml` (Day 01 scaffolding, per the stack table) and requires no
API key — it is a pure-Python, fully local, offline package.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. (This day consumes Day 03/04's chunks as-is; no re-chunking here.)
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata. `HybridQueryResult`/`Bm25QueryResult` must carry
  the full `Chunk` (not a stripped-down projection), so downstream reranking
  and citation-building never need to re-fetch metadata by ID.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess. (No
  LLM call this day, but a hybrid search that returns zero candidates from
  both indexes must return a well-defined empty result, not raise — Day 07's
  fallback path depends on being able to tell "genuinely no evidence" apart
  from "the retrieval layer crashed.")
- All LLM calls go through the provider fallback chain defined in CLAUDE.md
  — never hardcode a single provider. (Not applicable this day — no LLM
  call — but do not add one.)
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose. (Not applicable this day — no LLM call.) All of this day's own data
  models (`Bm25IndexStats`, `Bm25QueryResult`, `HybridQueryResult`) must
  themselves be Pydantic models per the established convention.
- Embedding and reranker models run locally by default — no paid API in the
  default path. `rank-bm25` is already local/offline; do not introduce a
  network call anywhere in this stage.
- **BM25 tokenization must be enforced in exactly one shared function**,
  used identically at build time and query time — the same discipline Day
  04 applied to L2-normalization (`_l2_normalize`, reached by every caller
  via `embed_texts`). Use the scheme decided above (lowercase, split on
  non-alphanumeric runs, no camelCase/snake_case sub-splitting in this
  version). Getting tokenization right on one side and different on the
  other silently degrades exact-match quality while still looking like it
  works.
- **The BM25 index and the vector index must be built over the same chunk
  set** (same repo-wide `collect_repo_chunks` output, same `Chunk.id`
  values). This must be enforced structurally, not just by convention — use
  the single `build_all_indexes` orchestration entry point (see "Files to
  change" above) rather than letting BM25 and vector builds each call
  `collect_repo_chunks` independently.
- The merge strategy (RRF, `k = 60`, 1-indexed rank) must be implemented so
  its scoring is inspectable per-result, not just a single opaque final
  number — this is what CLAUDE.md's Day 05 line means by "transparent
  scoring/reciprocal-rank."
- Do not silently drop a candidate that appears in only one of the two
  result lists — a chunk found only by BM25 (e.g. an exact symbol match with
  no close semantic neighbor) or only by the vector index (e.g. a
  semantically relevant chunk with no shared keywords) must still surface in
  the merged result, scored fairly relative to the other side.
- The BM25 index must be idempotent and persistent like the vector index:
  `build_index(...)` writes to `INDEX_DIR` (or a BM25-specific subpath
  within it), and a later process can `load_index(...)` without rebuilding.
  `rank-bm25`'s `BM25Okapi` has no native serialization format — persist it
  via `pickle` (tokenized corpus + BM25 state). **Only ever unpickle a file
  this process itself wrote to `INDEX_DIR`; never unpickle index data from
  an untrusted or externally-supplied path** — same untrusted-input
  discipline Day 02 applied to cloned repo content, stated explicitly here
  since pickle deserialization of arbitrary input is a known code-execution
  risk. Persisted files stay gitignored per FLOW.md's data-lifecycle table.
- **`rank-bm25` has no inverted index — `BM25Okapi.get_scores()` is a
  linear scan over every document in the corpus for every query call.**
  This is fine at this project's target scale (tens of thousands of
  chunks) but is worth a measured, not assumed, number: the manual smoke
  test below must record the wall-clock time of a BM25 query against the
  real fixture repo's full index. If it is not near-instant, log the
  observed number to `DECISIONS.md` as a known characteristic to revisit if
  Day 07's live per-question latency becomes noticeable — this is not a
  blocker for today, just a number that should be measured now rather than
  discovered later.
- Log every meaningful decision to `DECISIONS.md` — in particular, the
  merge strategy (RRF, `k = 60`, 1-indexed rank convention), the BM25
  persistence format (pickle, and the untrusted-input caveat above), the
  tokenization scheme (and the explicit camelCase/snake_case
  non-splitting limitation), the `build_all_indexes` single-orchestration
  decision, and the measured BM25 query latency from the smoke test.
- Update `FLOW.md` with the new/changed pipeline path: fill in the `F` (BM25
  index) box in the "Offline build flow" diagram and expand the "Online
  query flow" sequence diagram's `R-->>R: ANN over FAISS + BM25` step into
  the real hybrid-merge flow.

## Definition of done
- [ ] Given the same fixture chunk set used in Day 04's tests, BM25
      `build_index`/`load_index`/query round-trips correctly in a fresh
      process (same behavior contract as `indexing.vector`).
- [ ] BM25 tokenization is enforced in one shared function and is identical
      between build time and query time (directly tested, not just visually
      inspected); the lowercase/non-alphanumeric-split scheme is exercised
      by a test with mixed-case input.
- [ ] An exact-symbol query (e.g. a real function name from the fixture
      repo, phrased as just that identifier) surfaces the chunk defining
      that symbol at or near the top of the BM25-only result list.
- [ ] A natural-language/semantic query with little lexical overlap with the
      target chunk (e.g. "where do we limit concurrent operations") surfaces
      the semantically relevant chunk at or near the top of the vector-only
      result list, even though BM25 alone would rank it poorly.
- [ ] `hybrid_search(query, top_k)` returns a merged, ranked
      `list[HybridQueryResult]` where each result exposes its contributing
      BM25 rank/score and vector rank/score (present/absent per side,
      never a fabricated zero for a side that didn't return the chunk), and
      RRF's rank is confirmed 1-indexed by a direct test (e.g. the top
      result of a single-list case scores exactly `1/(60+1)`).
- [ ] A chunk that appears in both the BM25 and vector candidate lists
      outranks an otherwise-similar chunk that appears in only one list, in
      at least one concrete test case.
- [ ] Querying with an empty or nonsensical string against both indexes does
      not crash the process; it returns a well-defined (possibly empty or
      low-relevance) merged result set.
- [ ] `hybrid_search` against a query where neither index has any real
      match returns an explicitly empty result list (not an error, not a
      silently truncated one) — this is what Day 07's "not enough evidence"
      fallback will key off of.
- [ ] `build_all_indexes` calls `collect_repo_chunks` exactly once and
      builds both indexes from that single `list[Chunk]` — directly tested
      (e.g. via a spy/mock confirming a single call), not just assumed from
      reading the code.
- [ ] BM25 index persists under `INDEX_DIR` (or a subpath) and is gitignored
      — `git status` after a build shows nothing new tracked.
- [ ] **Manual smoke test against a real cloned repository** (the same
      `p-queue` fixture used in Days 03/04, or another small real repo):
      run `build_all_indexes` end to end, then run one exact-symbol query
      and one natural-language query through `hybrid_search` and confirm
      the merged top results are genuinely relevant with correct
      file/symbol/line citations — not just that fixture tests pass. Record
      the observed BM25 query wall-clock time from this run in
      `DECISIONS.md` per the linear-scan note above.
- [ ] Unit tests (`tests/test_indexing_bm25.py`, `tests/test_retrieval_hybrid.py`)
      cover build → persist → load → query for BM25 and the merge logic
      itself (including the semantic-vs-exact-symbol split CLAUDE.md calls
      out explicitly), and pass under `pytest`.
- [ ] `ruff check`, `ruff format --check`, and `mypy` all pass on the new
      code.
- [ ] `DECISIONS.md` has a new entry covering: the merge-strategy choice
      (RRF, `k = 60`, 1-indexed rank), the BM25 persistence format (pickle
      + untrusted-input caveat), the tokenization scheme (and its
      camelCase/snake_case limitation), the `build_all_indexes`
      single-orchestration decision, and the measured BM25 query latency;
      `FLOW.md`'s "Offline build flow" and "Online query flow" diagrams
      reflect the now-real BM25 + hybrid-merge path.