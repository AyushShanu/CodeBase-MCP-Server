# Spec: Reranker & Retrieval Quality

## Overview
Day 05 (`feature/hybrid-retrieval`) gave the pipeline `retrieval.hybrid.hybrid_search`,
which merges BM25 and vector candidates via Reciprocal Rank Fusion into one
ranked `list[HybridQueryResult]` — but RRF's fusion score is a rank-order
heuristic, not a judgment of how well a chunk actually answers the query. It
combines two lists that never look at the query and the chunk *together*.
CLAUDE.md's Day 06 line calls for exactly the fix: "Cross-encoder reranks a
larger hybrid candidate pool down to the strongest context" — a cross-encoder
jointly encodes `(query, chunk)` pairs and scores relevance directly, which is
strictly more expensive per-pair than RRF but far more accurate, which is why
it runs *after* RRF has already cut a large corpus down to a small candidate
pool rather than over every chunk in the index. This is the last retrieval-side
stage before generation (Day 07) — everything Day 07's LLM sees is whatever
this stage decides is the "strongest context," so a reranking bug here
degrades every downstream answer silently (still-plausible-looking retrieval,
worse actual evidence). Day 06 also asks for a 10–20 question benchmark set
("doubles as the V2 eval set") — this is the first point in the roadmap where
retrieval quality becomes something that can be checked repeatably rather than
eyeballed per query, and Day 11 depends on this set existing.

**A note on what "larger candidate pool" requires from the caller**: this
stage is only as good as the pool it's handed. If a caller feeds it Day 05's
`hybrid_search` output at its *default* `top_k`, RRF has already cut the
field down before the reranker ever sees it, defeating the point of this day.
See the explicit calling contract in "Rules for implementation" below — this
is not an implementation detail, it's the interface between Day 05 and Day 06
and must be documented as such.

## Depends on
- Day 01 — Foundation (package scaffold, config, CLI, MCP stub). Complete.
- Day 02 — GitHub Ingestion & File Filtering. Complete.
- Day 03 — Tree-sitter Parsing & AST Chunking (`chunker.models.Chunk`). Complete.
- Day 04 — Embeddings & Vector Index (`indexing.vector`). Complete.
- Day 05 — BM25 & Hybrid Retrieval (`retrieval.hybrid.hybrid_search`,
  `retrieval.models.HybridQueryResult`). Complete per
  `.claude/specs/05-hybrid-retrieval.md`. This day's reranker consumes
  `hybrid_search`'s output as its input candidate pool — it does not call
  either index directly, and does not re-implement or replace the RRF merge.
  **It requires the caller to have requested a wide pool from `hybrid_search`
  — see the calling-contract rule below.**

## Pipeline stage(s) touched
- **Reranking** (new logic — `reranker/__init__.py` is currently an empty
  placeholder with a docstring only, per D-007-style deferral).

No other stage (Ingestion, Parsing & Chunking, Embedding, BM25, Hybrid
Retrieval, Generation, Citations, MCP Server, Impact Analysis) is modified.
`retrieval.hybrid.hybrid_search` is consumed as-is; its RRF merge logic is not
changed. (Its logging is extended slightly — see "Files to change" — but its
scoring behavior is untouched.)

## MCP tools affected
No MCP tool changes. `search_code` lands in Day 08 and will call this day's
`rerank` internally (after calling Day 05's `hybrid_search` with a wide
`top_k` — see calling contract), but no MCP-facing code exists yet.

## Model / provider choice for this step
This day touches the **Reranker** row of CLAUDE.md's stack table, which lists
three local, no-API-key cross-encoder options:

- **Chosen: `cross-encoder/ms-marco-MiniLM-L-6-v2`** — CLAUDE.md's own
  "start here" default for this row, matching the same "start with the
  documented default, swap later if needed" pattern Day 04 used for
  `all-MiniLM-L6-v2`. It's a small (~80MB), well-established MS MARCO-trained
  cross-encoder with fast CPU inference, which matters here specifically
  because rerank latency is paid on every live query (unlike embedding/BM25
  build cost, which is paid once at index time).
- **Alternatives (documented, not built):** `bge-reranker-base` (larger,
  generally stronger multilingual relevance judgments, higher latency) and
  `mxbai-rerank-base-v1` (newer, competitive quality, less battle-tested at
  this project's scale). Both are swappable later purely by changing
  `RERANKER_MODEL_NAME` — no code change — since `CrossEncoder(model_name)`
  is the only place the model name is consumed.

Log this choice to `DECISIONS.md` per CLAUDE.md's "do not silently pick a
stack-table option" rule, the same way Day 05 logged the RRF-vs-weighted-sum
choice.

**No new pip dependency is required for the model itself** —
`sentence-transformers>=3.0` is already declared in `pyproject.toml` (Day 01
scaffolding, currently used transitively via `langchain-huggingface` for
embeddings), and `sentence_transformers.CrossEncoder` is part of that same
package. This should be logged to `DECISIONS.md` too, since "no new
dependency needed" for a stack-table row is itself worth stating explicitly
rather than leaving implicit.

**Score scale — a second decision this day must make and log:** `CrossEncoder
.predict()` for `ms-marco-MiniLM-L-6-v2` returns a raw, unbounded relevance
logit, not a `[0, 1]` probability and not comparable in scale to RRF's
`1 / (k + rank)` fusion score. Decision: **do not attempt to combine or
renormalize `rerank_score` against `HybridQueryResult.score`** — the
reranker's job is purely to re-*order* the candidate pool it's given (using
raw score for sort order and tie-breaking only), not to produce a fused
number. Both scores stay visible on the result model (see below) for
transparency, but only `rerank_score` determines the reranked order. This
mirrors Day 05's own reasoning for choosing RRF over weighted-sum (avoid
combining two incomparable scales) — applied one stage further downstream.

**Input length — a third decision this day must make and log:**
`ms-marco-MiniLM-L-6-v2`, like most MiniLM-based cross-encoders, has a
default max sequence length of 512 tokens for the combined `(query, passage)`
pair. A large chunk (a big function or class body from Day 03's chunking)
can exceed that. Decision: **pass an explicit `max_length=512` to
`CrossEncoder(model_name, max_length=...)`** rather than relying on whatever
the library's default happens to be, so the truncation point is a stated,
intentional value, not an implicit library default that could silently
change with a version bump. Truncation itself is accepted as-is for this
day — code chunks typically carry their identifying signature near the top,
so truncating the tail is a reasonable tradeoff — but this must be named
explicitly in `DECISIONS.md` as an accepted limitation, the same discipline
Day 05 applied to camelCase/snake_case tokenization. Do not add
chunk-splitting or summarization to work around it; that's out of scope for
this day.

## Files to change
- `src/codebase_rag_mcp/reranker/__init__.py` — replace the placeholder with
  real exports (`rerank`, `RerankedResult`, and the module's exceptions).
- `src/codebase_rag_mcp/config.py` — add `RERANKER_MODEL_NAME` (default
  `"cross-encoder/ms-marco-MiniLM-L-6-v2"`), `RERANKER_MAX_LENGTH` (default
  `512` — the explicit truncation point named above), and `RERANK_TOP_N`
  (default `8` — how many results `rerank` returns after reordering,
  deliberately smaller than `HYBRID_CANDIDATE_POOL_SIZE`/the wide `top_k`
  callers pass into `hybrid_search` before reranking), alongside the
  existing `RRF_K`/`HYBRID_CANDIDATE_POOL_SIZE` settings.
- `.env.example` — document the new reranker settings under a "Reranker
  settings" section, defaulted and commented like the existing entries, and
  note the two documented-but-unwired alternative models per CLAUDE.md's
  table (matching how the embeddings section already documents its
  alternatives).
- `src/codebase_rag_mcp/retrieval/hybrid.py` — add one `logger.info` call at
  the end of `hybrid_search` logging the BM25/vector/merged candidate counts
  (currently only `logger.warning` exists, for the unavailable-index case).
  This is what CLAUDE.md's Day 06 line means by "log retrieval stages" for
  the hybrid stage specifically — a pure logging addition, no behavior
  change to the merge itself.
- `FLOW.md` — the "Online query flow" sequence diagram in Section 3 already
  sketches a generic `M->>X: rerank(query, candidates)` / `X-->>M: top_n
  chunks` step anticipating this day; replace it with the real module path
  (`reranker.rerank:rerank`), note the score-scale decision (rerank_score
  vs. hybrid score, not combined) inline, and **show the caller passing a
  wide `top_k` into `hybrid_search` before the rerank step** — the diagram
  must make the pool/top_k contract visually obvious, not just documented in
  prose someone could miss.

## Files to create
- `src/codebase_rag_mcp/reranker/rerank.py` — the reranking entry point:
  given a query string and a `list[HybridQueryResult]` (Day 05's output,
  *not* raw chunks — this stage must never re-fetch or re-derive chunk
  content, only reorder what it's given), run each `(query, chunk.content)`
  pair through `CrossEncoder(model_name, max_length=RERANKER_MAX_LENGTH)
  .predict(...)`, sort by `rerank_score` descending, and return the top
  `RERANK_TOP_N` as `list[RerankedResult]`. Loads a fresh `CrossEncoder`
  instance per call — same convention Day 04's `embed_texts` and Day 05's
  `hybrid_search` already use (no cross-call caching yet; Day 08's MCP
  server owns model/index lifecycle once it exists) — do not introduce
  caching here that the rest of the codebase doesn't have yet. **If
  `candidates` has fewer than `HYBRID_CANDIDATE_POOL_SIZE` entries, this is
  not an error** (a genuinely small repo may have fewer real matches than
  the configured pool size) — `rerank` must never assume or validate a
  minimum input size, only ever cap at `RERANK_TOP_N`.
- `src/codebase_rag_mcp/reranker/models.py` — `RerankedResult` (Pydantic,
  `frozen=True` config matching `HybridQueryResult`'s convention): wraps the
  full input `HybridQueryResult` (never a stripped projection — chunk,
  bm25_rank/score, vector_rank/score all stay visible) plus `rerank_score:
  float` and `rerank_rank: int` (1-indexed position after reordering, same
  1-indexed convention Day 05 established for RRF).
- `src/codebase_rag_mcp/reranker/exceptions.py` — `RerankerError` base and
  `RerankerModelError` (model fails to load or `.predict()` raises),
  mirroring `indexing.exceptions.IndexingError`/`EmbeddingModelError`.
- `tests/test_reranker.py` — unit tests for `rerank`, mirroring
  `tests/test_retrieval_hybrid.py`'s structure: build small fixture
  `HybridQueryResult`s (no real GitHub clone or real model download needed
  for most cases — mock/monkeypatch `CrossEncoder.predict`), and cover the
  cases in "Definition of done" below.
- `benchmarks/questions.json` — the 10–20 question benchmark set CLAUDE.md's
  Day 06 line asks for ("doubles as the V2 eval set"). **This file is
  committed to the repo like any other source file — it is not build
  output, not gitignored, unlike everything under `INDEX_DIR`/`DATA_DIR`.**
  Written against the `p-queue` fixture repo already used for Days 03–05's
  manual smoke tests (the only real repo indexed consistently so far —
  CLAUDE.md's four "non-negotiable demo questions" are auth/login-flow-
  specific and target whichever repo is chosen as the final demo in Day
  09/12, which is a separate, smaller, fixed set; this benchmark set is
  broader and repo-specific, meant for retrieval-quality regression
  checking, and can be extended or replaced once the final demo repo is
  picked). Each entry: `id`, `question`, `category` (one of `exact_symbol`,
  `semantic`, `structural` — matching CLAUDE.md's Day 05 "test semantic vs.
  exact-symbol queries separately" split, extended with `structural` for
  file/module-level questions that aren't about one specific symbol, e.g.
  "what does the `retrieval` module export" or "which file defines the
  queue's public API"), `expected_file` (repo-relative path),
  `expected_symbol` (optional), `notes` (free text). This file is data, not
  test code — Day 11 reads it to run the benchmark programmatically; Day 06
  only needs it to exist and be exercised manually (see Definition of done).

## New dependencies
No new dependencies. `sentence-transformers>=3.0` (declared in `pyproject.toml`
at Day 01 scaffolding) already provides `sentence_transformers.CrossEncoder`,
and `cross-encoder/ms-marco-MiniLM-L-6-v2` requires no API key — it downloads
once from the Hugging Face Hub (same mechanism Day 04's embedding model
already uses) and runs fully locally/offline after that.

## Rules for implementation
- **Calling contract with Day 05 (the fix this spec revision adds): any
  caller intending to rerank `hybrid_search`'s output MUST call
  `hybrid_search(query, top_k=HYBRID_CANDIDATE_POOL_SIZE, ...)` (i.e. pass
  the wide pool size as `top_k`), never rely on `hybrid_search`'s own
  default `top_k`.** `hybrid_search`'s default `top_k` truncates the merged
  RRF list *before* reranking ever sees it — feeding `rerank` an
  already-narrowed default-`top_k` result defeats the entire purpose of
  this stage (CLAUDE.md: "reranks a *larger* hybrid candidate pool"). This
  is not an implementation nicety, it is the interface contract between Day
  05 and Day 06, and it must be: (a) stated here in prose, (b) visible in
  the updated `FLOW.md` sequence diagram, (c) exercised exactly this way in
  the manual smoke test and in `tests/test_reranker.py`'s integration-style
  case, and (d) called out again in Day 08's spec when `search_code` wires
  the two functions together, so it survives the handoff to whoever
  implements that day.
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. (This day consumes Day 03's chunks, via Day 05's `HybridQueryResult`,
  as-is; no re-chunking here.)
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata. `RerankedResult` must carry the full
  `HybridQueryResult` (and therefore the full `Chunk`), never a stripped
  projection — Day 07's citation-building must not need to re-fetch metadata
  by ID.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess. (No LLM
  call this day, but `rerank` given an empty candidate list must return an
  empty result, not raise — Day 07's fallback path depends on this the same
  way it depends on Day 05's empty-hybrid-result contract.)
- All LLM calls go through the provider fallback chain defined in CLAUDE.md —
  never hardcode a single provider. (Not applicable this day — no LLM call —
  but do not add one.)
- Structured outputs via Pydantic for any LLM call that isn't freeform prose.
  (Not applicable — no LLM call.) `RerankedResult` itself must be a Pydantic
  model per the established convention.
- Embedding and reranker models run locally by default — no paid API in the
  default path. `cross-encoder/ms-marco-MiniLM-L-6-v2` runs fully on-device;
  do not introduce a network call anywhere in this stage beyond the one-time
  Hugging Face Hub model download.
- **`rerank_score` and `HybridQueryResult.score` (RRF) must never be added,
  averaged, or otherwise combined into a single number.** They live on
  different, incomparable scales (see "Model / provider choice" above) —
  `rerank_score` determines the output order; the RRF score stays on the
  result purely for transparency/debugging.
- **The reranker must never re-embed, re-tokenize, or otherwise recompute
  anything Day 04/05 already produced.** It consumes `HybridQueryResult.chunk
  .content` directly as the text half of each `(query, chunk)` pair fed to
  the cross-encoder.
- **Cross-encoder input length is capped at an explicit `RERANKER_MAX_LENGTH`
  (default 512 tokens), passed directly to `CrossEncoder(...)`, not left to
  the library's implicit default.** Truncation of long chunks is an accepted
  limitation for this day (see "Model / provider choice" above) — log it,
  do not silently rely on undocumented default behavior.
- Do not silently truncate or drop candidates before scoring — every
  candidate passed into `rerank` must get a `CrossEncoder.predict()` call
  (batched in one call, not one predict per pair — mirror Day 04's
  `embed_texts`, which does one batched `embed_documents` call rather than
  one call per text) before the top-`RERANK_TOP_N` cut happens.
- Log every meaningful decision to `DECISIONS.md` — in particular: the
  `cross-encoder/ms-marco-MiniLM-L-6-v2` choice (and the two documented
  alternatives), the "no new dependency needed" note, the score-scale
  decision (rerank_score vs. RRF score never combined), the explicit
  `RERANKER_MAX_LENGTH`/truncation-accepted decision, the no-caching
  convention (consistent with Day 04/05), the hybrid_search calling
  contract, and the two measured latency numbers from the manual smoke test
  below (model construction time and batch inference time, kept separate).
- Update `FLOW.md` with the new/changed pipeline path: replace Section 3's
  generic `rerank(query, candidates)` placeholder step with the real module
  path, the score-scale note, and the wide-`top_k` calling contract.

## Definition of done
- [ ] `rerank(query, candidates, top_n=...)` takes a `list[HybridQueryResult]`
      and returns a `list[RerankedResult]` of length `min(top_n,
      len(candidates))`, sorted by `rerank_score` descending, each entry's
      `rerank_rank` confirmed 1-indexed by a direct test.
- [ ] Every `RerankedResult` exposes the full underlying `HybridQueryResult`
      (chunk + bm25/vector rank/score) alongside its own `rerank_score`/
      `rerank_rank` — verified by a test that reads both the original and
      reranked fields off the same result.
- [ ] Reranking demonstrably changes order vs. the input hybrid order in at
      least one concrete test case (e.g. a candidate ranked lower by RRF but
      more textually/semantically relevant to the query moves up after
      rerank) — this is the actual "retrieval quality" improvement this day
      claims to deliver, so it must be shown, not assumed.
- [ ] A test exercises the full intended call chain — `hybrid_search(query,
      top_k=HYBRID_CANDIDATE_POOL_SIZE)` followed by `rerank(...)` — and
      confirms the reranked output can include a candidate that would have
      been cut off by `hybrid_search`'s *default* `top_k`, directly proving
      the calling-contract rule matters and is followed correctly.
- [ ] `rerank(query, [])` returns `[]` without raising; `rerank` with fewer
      candidates than `HYBRID_CANDIDATE_POOL_SIZE` (a small-repo case) does
      not raise or warn — only ever caps at `top_n`.
- [ ] `CrossEncoder.predict()` is called exactly once per `rerank()` call
      with the full batch of `(query, chunk.content)` pairs — not once per
      candidate — directly tested (e.g. via a spy/mock confirming call
      count), mirroring Day 04's batched-embedding requirement.
- [ ] `CrossEncoder` is constructed with `max_length=RERANKER_MAX_LENGTH`
      explicitly — directly tested (e.g. asserting the constructor is
      called with that kwarg via a spy/mock), not just visually inspected.
- [ ] `rerank_score` and the original `HybridQueryResult.score` are never
      combined into a new field anywhere in `reranker/` — confirmed by
      reading `models.py`/`rerank.py` (structural, not a runtime assertion).
- [ ] `benchmarks/questions.json` exists with 10–20 entries covering all
      three categories (`exact_symbol`, `semantic`, `structural`), each with
      a real `expected_file` path that exists in the `p-queue` fixture repo,
      and is committed as a tracked source file (confirmed via `git status`
      / `git add`, not left gitignored alongside index build output).
- [ ] **Manual smoke test against the real `p-queue` fixture repo** (same one
      used in Days 03–05): call `hybrid_search(query, top_k=
      HYBRID_CANDIDATE_POOL_SIZE)` (the wide pool, per the calling contract
      — not the default `top_k`) for at least 3 of the
      `benchmarks/questions.json` entries (one per category), pipe the
      results through `rerank`, and confirm the top-ranked chunk after
      reranking is genuinely more relevant than the top hybrid-only result
      would have been for at least one semantic-category question. Record
      two separate wall-clock numbers in `DECISIONS.md`: `CrossEncoder(...)`
      construction time, and the batched `.predict()` call time for a
      ~50-candidate batch — kept separate because construction cost is very
      likely the larger of the two and Day 08's caching priorities need to
      know which.
- [ ] Unit tests (`tests/test_reranker.py`) cover build → score → sort →
      truncate for `rerank`, including the empty-input, fewer-than-pool-size,
      batched-predict-call-count, explicit-`max_length`, and
      calling-contract cases above, and pass under `pytest`.
- [ ] `ruff check`, `ruff format --check`, and `mypy` all pass on the new
      code.
- [ ] `DECISIONS.md` has a new entry covering: the reranker model choice
      (`cross-encoder/ms-marco-MiniLM-L-6-v2` + the two documented
      alternatives), the "no new dependency" note, the score-scale decision,
      the explicit `RERANKER_MAX_LENGTH`/truncation-accepted decision, the
      no-caching convention, the `hybrid_search` wide-`top_k` calling
      contract, and the two measured latency numbers (construction vs.
      inference); `FLOW.md`'s "Online query flow" sequence diagram reflects
      the now-real rerank step and visibly shows the wide-`top_k` call into
      `hybrid_search` feeding it.