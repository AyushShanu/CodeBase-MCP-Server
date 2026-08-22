# Spec: Embeddings & Vector Index

## Overview
Day 03 (tree-sitter-chunking) produces `Chunk` objects one file at a time —
`parse_file` → `chunk_file` — with no repo-wide aggregation and nothing that
turns chunk text into a searchable index. This day is the first stage that
actually needs "all chunks for the repo" as a single collection: it adds the
repo-wide orchestration glue (looping `ingestion.scan` → `parser.parse_file`
→ `chunker.chunk_file` over every included `FileRecord` and collecting the
result into one `list[Chunk]`), wires a local embedding model
(`all-MiniLM-L6-v2`, per CLAUDE.md) to turn each chunk's content into a dense
vector, and stores those vectors in a persistent FAISS index alongside a
parallel chunk-metadata store so a vector ID can always be resolved back to
its exact file path, symbol, and line range. This is the first point in the
pipeline where a query can retrieve anything — Day 05 (BM25/hybrid
retrieval) and everything after it depends on this stage's persisted index
and metadata store existing and being loadable.

## Depends on
- Day 01 — Foundation (package scaffold, config, CLI, MCP stub). Complete.
- Day 02 — GitHub Ingestion & File Filtering (`ingestion.loader`,
  `ingestion.scanner`, `ingestion.models.FileRecord`/`RepoStats`). Complete.
  This day's repo-wide loop consumes `RepoStats.files` (path + language +
  `included`) as its file list.
- Day 03 — Tree-sitter Parsing & AST Chunking (`parser.parse_file`,
  `chunker.chunk_file`, `chunker.models.Chunk`). Complete per
  `.claude/specs/03-tree-sitter-chunking.md`. This day consumes `Chunk` as
  its embedding unit; it does not change parsing or chunking logic itself.

## Pipeline stage(s) touched
- **Embedding** (new logic — `indexing/vector.py` is currently a placeholder
  per D-007).
- A small amount of new **repo-wide orchestration** glue (not itself a named
  pipeline stage in CLAUDE.md, but explicitly called out as this day's
  responsibility in the Day 03 spec's "out of scope" section) — a function
  that loops ingestion → parsing → chunking over an entire `RepoStats` and
  returns one aggregated `list[Chunk]`, feeding the embedding stage.

No other stage (Ingestion, Parsing & Chunking's own logic, BM25, Hybrid
Retrieval, Reranking, Generation, Citations, MCP Server, Impact Analysis) is
touched. BM25 stays a placeholder until Day 05.

## MCP tools affected
No MCP tool changes. `search_code`, `find_symbol`, `get_file_context`,
`analyze_impact`, and `repository_summary` all land in Day 08+ and will
consume this stage's vector index later, but nothing MCP-facing exists yet.

## Model / provider choice for this step
This day touches the **Embeddings** and **Vector store** rows of CLAUDE.md's
stack table. Per CLAUDE.md, embeddings and vector store are local-only —
no paid API in this path.

- **Embeddings — Primary:** `all-MiniLM-L6-v2`, per CLAUDE.md's explicit
  "start here, Day 4" guidance. Loaded via
  `langchain_huggingface.HuggingFaceEmbeddings` (already a declared
  dependency in `pyproject.toml`), which wraps `sentence-transformers`
  under the hood. **Call it with `normalize_embeddings=True` (or normalize
  the returned vectors explicitly if the wrapper doesn't expose that
  kwarg)** — see the FAISS index-type decision below for why this isn't
  optional.
  - **Documented alternatives:** `jina-embeddings-v2-base-code` (CLAUDE.md
    calls this out as the intended swap-in "before final" — i.e. later,
    likely around Day 09/11 polish, not this day) and `bge-small-en-v1.5`.
    Neither is wired this day; `EMBEDDING_MODEL_NAME` should be a named
    constant/config value (not hardcoded inline) specifically so that later
    swap is a one-line change, not a rewrite.

- **Vector index implementation — Primary: hand-rolled, raw `faiss-cpu`
  (`IndexFlatIP`) plus a custom chunk-metadata store, not
  `langchain_community.vectorstores.FAISS`.** This is the central
  implementation decision of the day and must be logged to DECISIONS.md
  with this reasoning, the same way Day 02's clone mechanism and Day 03's
  hand-rolled-walk choice were:
  - **Why not the LangChain wrapper**, even though it was the reference
    snippet shown when this stack was first sketched
    (`langchain_community.vectorstores.FAISS` + `HuggingFaceEmbeddings`):
    its docstore is keyed on LangChain `Document` objects
    (`page_content`/`metadata` dict), not this project's own typed
    `Chunk` Pydantic model — round-tripping every `Chunk` field losslessly
    through that shape means either fighting the wrapper's assumptions or
    converting back and forth at every boundary. It also manages
    normalization somewhat opaquely depending on which distance strategy
    is configured, which conflicts with wanting explicit, directly-tested
    control over normalization (see below). A hand-rolled layer keeps
    `Chunk` the single source of truth end to end and is consistent with
    Day 03's own precedent of hand-rolling where precise control matters
    more than reuse.
  - **Index type:** `IndexFlatIP` (flat, inner-product) over
    L2-normalized vectors — the standard, mathematically correct way to
    get cosine-similarity search out of FAISS (inner product of two
    unit-length vectors equals their cosine similarity). No need for
    `IndexIVFFlat` or quantization at this corpus scale (tens of thousands
    of chunks is trivial for brute-force search on CPU).
  - **Normalization is a hard requirement, not an implementation detail
    left to chance:** every vector added to the index, and every query
    vector at search time, must be L2-normalized before use. Getting this
    right on one side and wrong on the other is the single easiest way to
    silently turn "cosine similarity search" into "similarity biased by
    chunk length/content magnitude" while still returning results that
    look plausible enough to pass a casual smoke test — see Definition of
    Done for the explicit test this requires.
  - **Documented alternatives:** Qdrant (local Docker or free cloud tier —
    CLAUDE.md's own "optional add-on for Day 4/12" note for a stronger
    resume line, explicitly deferred) and Chroma. Not used this day: FAISS
    has no extra service/Docker dependency, matches D-003's prior
    acceptance, and keeps the default path fully offline with zero new
    infrastructure.

## Files to change
- `src/codebase_rag_mcp/indexing/__init__.py` — replace the placeholder with
  real exports (`build_index`, `VectorIndex`, `embed_chunks`, or equivalent
  names chosen during implementation).
- `src/codebase_rag_mcp/indexing/vector.py` — replace the placeholder
  docstring-only module with the real FAISS build/persist/load/query
  implementation.
- `src/codebase_rag_mcp/config.py` — add `EMBEDDING_MODEL_NAME` (default
  `"all-MiniLM-L6-v2"`) alongside the existing `INDEX_DIR` setting, so the
  model name is swappable via `.env` without code changes.
- `.env.example` — document `EMBEDDING_MODEL_NAME` under a new "Local model
  settings" or "Embedding settings" section, defaulted and commented like
  the existing `INDEX_DIR`/`DATA_DIR` entries.
- `pyproject.toml` — no dependency additions expected (see below), but
  confirm `sentence-transformers`/`langchain-huggingface`/`faiss-cpu`
  versions are sufficient; bump only if a real incompatibility surfaces
  during implementation, and log that as a decision if it happens.

## Files to create
- `src/codebase_rag_mcp/indexing/models.py` — Pydantic models for this
  stage's inputs/outputs, matching the `parser.models`/`chunker.models`
  convention:
  - `IndexedChunk` (or similar): wraps a `Chunk` plus its assigned integer
    FAISS vector ID, so metadata lookups by vector ID are O(1).
  - `VectorIndexStats`: counts (chunks embedded, chunks skipped + reason,
    embedding dimension, index size) returned from a build call, for
    CLI/logging visibility.
- `src/codebase_rag_mcp/indexing/repo.py` (or fold into `vector.py` if small
  enough — decide during implementation, but keep it a separate, directly
  testable function either way): the repo-wide orchestration loop —
  `ingestion.scanner.scan` (or an already-resolved `RepoStats`) → for each
  included `FileRecord`, read file bytes → `parser.extractor.parse_file` →
  `chunker.chunker.chunk_file` → aggregate into one `list[Chunk]`. This is
  the piece explicitly deferred by the Day 03 spec. **The per-file read
  step is new I/O that Day 02/03's existing resilience doesn't cover** —
  `parse_file`/`chunk_file` already guarantee a single bad *parse* never
  aborts a whole-repo run, but nothing yet guarantees the same for a file
  that's unreadable at *read* time (deleted, permission-denied, or
  otherwise gone between ingestion's scan and this stage running — rare
  against a shallow clone, more plausible against an actively-edited local
  path). Wrap each file's read in its own try/except, record the failure
  (file path + reason) rather than raising, and continue to the next file.
- `src/codebase_rag_mcp/indexing/exceptions.py` — module-specific exceptions
  (e.g. `EmbeddingModelError`, `IndexNotBuiltError` for querying before a
  build/load), matching the `ingestion.exceptions`/`parser.exceptions`
  convention already established.
- `tests/test_indexing_vector.py` (or `tests/test_indexing.py`) — unit tests
  for embed → build → persist → load → query, using a small in-memory chunk
  fixture set (no real GitHub clone needed).

## New dependencies
No new dependencies. `faiss-cpu`, `sentence-transformers`, and
`langchain-huggingface` are already declared in `pyproject.toml` (added at
Day 01 scaffolding, per the stack table) and require no paid API key —
`all-MiniLM-L6-v2` downloads once from the Hugging Face Hub on first use and
runs entirely on-device afterward. `langchain-community` is deliberately
**not** added — see the hand-rolled-vs-wrapper decision above.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. (This day consumes Day 03's chunks as-is; do not re-split or
  re-chunk chunk content when preparing embedding input.)
- Every retrieved chunk and every citation carries exact file path + start/end
  line metadata. The FAISS-ID → `Chunk` metadata store must round-trip every
  field on `Chunk` (`id`, `repo`, `file`, `symbol`, `type`, `language`,
  `start_line`, `end_line`, `content`) without loss — a citation built from a
  query result must be indistinguishable from one built from the original
  `Chunk`.
- **Embedding vectors must be L2-normalized before being added to the
  index, and query vectors must be normalized identically before search.**
  Both sides, always — this is what makes `IndexFlatIP` a correct
  cosine-similarity search rather than a magnitude-biased one. Enforce it
  in one place (the embed function), not ad hoc at each call site.
- **Chunks must be embedded in batches, not one at a time in a per-chunk
  loop.** Call the embedding model with a list of texts
  (`model.encode(texts, batch_size=...)` or the `HuggingFaceEmbeddings`
  equivalent) — at the "tens of thousands of chunks" scale this spec's own
  Definition of Done assumes, a naive per-chunk loop on a laptop CPU is the
  difference between an index build taking seconds and one taking many
  minutes. Log the chosen batch size to DECISIONS.md.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess.
  (Not directly exercised this day — no LLM call here — but the index this
  day builds is what Day 07's fallback path will query against, so an empty
  or corrupt index must fail loudly, not silently return zero results that
  get misread as "no evidence exists.")
- All LLM calls go through the provider fallback chain defined in CLAUDE.md
  — never hardcode a single provider. (Not applicable this day — no LLM
  call — but do not add one.)
- Structured outputs via Pydantic for any LLM call that isn't freeform prose.
  (Not applicable this day — no LLM call.) All of this day's own data models
  (`IndexedChunk`, `VectorIndexStats`) must themselves be Pydantic models
  per the `parser.models`/`chunker.models` convention already in the repo.
- Embedding and reranker models run locally by default — no paid API in the
  default path. `all-MiniLM-L6-v2` runs on-device via `sentence-transformers`;
  do not add a cloud embedding API call as the default path.
- The index build must be idempotent and persistent: `build_index(...)`
  writes to `INDEX_DIR` (per `config.INDEX_DIR`), and a later process can
  `load_index(...)` without rebuilding. Persisted index/metadata files stay
  gitignored per FLOW.md's existing data-lifecycle table — do not commit
  anything under `data/`.
- Do not silently drop chunks. If a chunk's `content` is empty or embedding
  fails for one chunk, log/skip it explicitly with a clear reason surfaced in
  `VectorIndexStats`, rather than letting the whole build fail or silently
  shrinking the index with no signal. The same applies to a file that fails
  to *read* during repo-wide orchestration (see `indexing/repo.py` above) —
  record it, don't crash the build.
- Log every meaningful decision to `DECISIONS.md` — in particular, the
  hand-rolled-vs-`langchain_community.vectorstores.FAISS` choice, the FAISS
  index type chosen (flat vs. IVF, L2 vs. inner-product), the normalization
  requirement, and the embedding batch size chosen.
- Update `FLOW.md` with the new/changed pipeline path: fill in the
  currently-aspirational `E`/`G` boxes in the "Offline build flow" diagram
  (embedding, persisted vector index) and the repo-wide orchestration step
  that now sits between chunking and embedding.

## Definition of done
- [ ] Given a small real repository (e.g. the same `p-queue` fixture used in
      Day 03's manual smoke test), the repo-wide orchestration function
      produces a `list[Chunk]` whose count matches manually running
      `parse_file`/`chunk_file` per included file and summing.
- [ ] A fixture where one included file is deleted/made unreadable between
      the `RepoStats` scan and the orchestration run does not crash the
      build — the failure is recorded (file path + reason) and every other
      file's chunks are still produced.
- [ ] `embed_chunks`/`build_index` produces one embedding vector per chunk,
      with vector dimensionality matching `all-MiniLM-L6-v2`'s known output
      size (384).
- [ ] Every vector actually added to the index has L2 norm ≈ 1.0 (assert
      this directly, not just "the code calls normalize"), and a query
      vector built the same way is normalized identically before search.
- [ ] Two near-duplicate chunks (e.g. the same function body with only a
      variable renamed) score a cosine similarity close to 1.0 against each
      other, and a semantically unrelated chunk scores meaningfully lower —
      a direct, concrete check that the index is actually doing
      cosine-similarity search, not just running without error.
- [ ] Embedding calls are batched (assert via a spy/mock that the embedding
      model is invoked with multiple texts per call, not once per chunk, for
      a fixture set of more than one chunk).
- [ ] `build_index(...)` persists a FAISS index file plus a chunk-metadata
      store under `INDEX_DIR`; both are gitignored and not accidentally
      committed (`git status` after a build shows nothing new tracked).
- [ ] A fresh process calling `load_index(...)` against that persisted
      `INDEX_DIR` (no rebuild) returns a queryable index with the same chunk
      count as the original build.
- [ ] Querying the loaded index with a natural-language string relevant to
      known content in the fixture repo (e.g. "queue concurrency limit")
      returns nearest-neighbor chunks whose `file`/`symbol`/`start_line`/
      `end_line` correctly identify real, relevant code in that repo — not
      just "runs without error."
- [ ] Querying with an empty or nonsensical string does not crash the
      process; it returns a well-defined (possibly low-relevance) result set
      or a clear error, never a silent empty success masking a real failure.
- [ ] Unit tests (`tests/test_indexing_vector.py` or equivalent) cover
      embed → build → persist → load → query using an in-memory `Chunk`
      fixture set, and pass under `pytest`.
- [ ] `ruff check`, `ruff format --check`, and `mypy` all pass on the new
      code.
- [ ] `DECISIONS.md` has a new entry covering the hand-rolled-vs-LangChain
      choice, the FAISS index type, the normalization requirement, and the
      embedding batch size; `FLOW.md`'s "Offline build flow" diagram and
      prose reflect the now-real embedding + persisted-index path instead
      of the current "aspirational" note.