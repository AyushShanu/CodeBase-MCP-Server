# Spec: Symbols, References & Impact Analysis (V2)

## Overview
Days 1–9 gave the pipeline AST-aware symbol *definitions* (Tree-sitter,
Day 03) and a `find_symbol` MCP tool that looks those definitions up by
name (Day 08) — but nothing in the codebase today records where a symbol
is *called* or *imported from*. This day adds that missing layer: a
lightweight, name-based reference/import scanner built on the same
already-parsed Tree-sitter tree, a persisted repo-wide reference index
alongside the existing vector/BM25 indexes, and a new `analyze_impact`
MCP tool that combines symbol-definition lookup + caller lookup +
importer lookup into deterministic evidence, then (only if that evidence
is non-empty) asks the LLM fallback chain to narrate it in prose. This is
the feature that answers demo question 4 — *"What would break if I change
`generateToken()`?"* — the one demo question explicitly marked `(V2)` in
CLAUDE.md, and is the reason `find_symbol`'s own docstring already says
"reference tracking is Day 10's `analyze_impact` scope."

## Depends on
- **Day 03 (Tree-sitter Parsing & AST Chunking)** — `parser/extractor.py`'s
  cached grammars and existing per-language Tree-sitter tree; the
  reference scanner reuses the same parsed `tree` object, it does not
  reparse.
- **Day 04/05 (Embeddings/BM25) + `indexing/repo.py`'s
  `collect_repo_chunks`/`build_all_indexes`** — the repo-wide walk this
  day's reference collection plugs into (same "collect once, build many"
  shape as D-020 established for vector+BM25).
- **Day 07 (LLM Generation & Citations)** — `generation.providers.registry
  .select_providers()`'s already-decided NVIDIA → Groq → OpenRouter →
  Gemini → Local fallback chain (D-022) and the
  extract-JSON-then-Pydantic-validate pattern in `generation/pipeline.py`;
  this day's LLM step reuses that chain and pattern, it does not
  re-decide provider precedence.
- **Day 08 (MCP Server V1)** — `mcp/server.py`'s `_ServerState`/`lifespan`
  caching pattern and `_match_symbol`'s exact+qualified-suffix matching
  convention (D-013/D-023), which this day extracts into a shared helper
  (see "Rules for implementation") rather than duplicating.

## Pipeline stage(s) touched
Parsing & Chunking (extended, not replaced), a new persisted index stage
(references — sibling to Embedding/BM25, not a replacement for either),
and Impact Analysis (new). MCP Server is extended with one new tool.

## MCP tools affected
- **`analyze_impact`** — new (V2). Given a symbol name, returns its
  definition(s), direct callers, importing files, and (only when
  deterministic evidence is non-empty) an LLM-generated prose
  explanation. Never fabricates a caller/importer not present in the
  deterministic evidence — see Rules.
- **`find_symbol`** — unchanged behavior/schema, but its internal
  `_match_symbol` matching logic is extracted into a shared helper (see
  Rules) so `analyze_impact` reuses the exact same exact/qualified-suffix
  semantics instead of a second, possibly-diverging implementation.
  Existing `find_symbol` tests must pass unchanged — this is a pure
  refactor, not a behavior change.
- `search_code`, `get_file_context`, `ask` — no changes.

## Model / provider choice for this step
No new embedding/reranker/vector-store decision here — this day reuses
Day 04/06's already-decided local embedding (`all-MiniLM-L6-v2`) and
reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`) only insofar as neither
is touched at all; the reference index is a plain lookup table, not a
vector/BM25 index, so it needs no embedding or ranking model.

The LLM step (`impact/explain.py`) reuses Day 07/D-022's already-decided
provider fallback chain (NVIDIA NIM → Groq → OpenRouter, + optional
Gemini/local per `config.py`) unchanged — **do not** introduce a second,
separate provider selection for this feature. If a change to that chain
seems warranted, flag it to the user rather than silently deciding it
here, per CLAUDE.md's own instruction not to re-decide something
DECISIONS.md already settled.

## Files to change
- `src/codebase_rag_mcp/parser/models.py` — add `ReferenceKind` (StrEnum:
  `CALL`, `IMPORT`) and `RawReference` (`name`, `kind`, `line`,
  `module: str | None`); add `ParseResult.references: list[RawReference]
  = Field(default_factory=list)`.
- `src/codebase_rag_mcp/parser/extractor.py` — add a second walk per
  language family (`_extract_references_ts_js`, `_extract_references_python`)
  run against the *same* already-parsed `tree.root_node` right after the
  existing symbol walk inside `parse_file` (no second Tree-sitter parse).
  TS/JS: `call_expression` → callee bare name (member calls like
  `obj.method()` capture the trailing `.property` identifier, matching
  `find_symbol`'s bare/suffix convention) at 1-indexed line;
  `import_statement` → the module specifier string. Python: `call` →
  callee bare name (`Name` or trailing `Attribute.attribute`);
  `import_statement`/`import_from_statement` → dotted module name. Never
  raises for a node shape it doesn't recognize — same
  parse-errors-not-exceptions discipline `parse_file` already uses for
  symbol extraction.
- `src/codebase_rag_mcp/indexing/repo.py` — extend the existing
  `collect_repo_chunks` loop (which already calls `parse_file` once per
  file) to also collect each file's `ParseResult.references` into
  `RepoChunkCollection.references: list[FileReference]` (a `RawReference`
  plus the same repo-relative `file` string `Chunk.file` uses). Extend
  `build_all_indexes` to also call `indexing.references.build_index` and
  persist it, alongside the existing vector/BM25/manifest writes — same
  "collect once (`collect_repo_chunks`), build many" shape D-020
  established, not a second repo walk.
- `src/codebase_rag_mcp/mcp/server.py` — add the `analyze_impact` tool;
  extend `_ServerState`/`_make_lifespan` to also load the persisted
  reference index (`indexing.references.load_index`), tolerating absence
  the same lenient way `manifest.load_manifest` does (a missing/older
  index degrades `analyze_impact` to "definitions only, no
  callers/importers," not a server-startup failure — every other tool
  works fine without it). Extract `_match_symbol`'s matching logic into
  the new shared `impact/symbols.py` helper (see Rules); `_match_symbol`
  itself becomes a thin wrapper, or is removed in favor of calling the
  shared helper directly — either way `find_symbol`'s existing tests must
  keep passing unmodified.
- `src/codebase_rag_mcp/indexing/exceptions.py` — add
  `ReferenceIndexLoadError` for a corrupt/unreadable persisted
  `references.json` (parity with `IndexLoadError`/`Bm25LoadError`). Do
  **not** add a "not built" exception — an absent reference index is a
  normal, lenient case (see above), matching `load_manifest`'s
  `None`-on-absence convention, not `vector.load_index`'s strict
  raise-on-absence convention.

## Files to create
- `src/codebase_rag_mcp/indexing/references.py` — `build_index(references:
  list[FileReference]) -> ReferenceIndex` (a plain grouped lookup:
  referenced bare name → list of `(file, line, kind, module)`) and
  `load_index`/persistence as plain JSON (`references.json`) — no pickle
  needed, unlike `bm25.py`'s `BM25Okapi`, since `RawReference`/
  `FileReference` are already-trivially-JSON Pydantic models.
- `src/codebase_rag_mcp/impact/symbols.py` — the shared exact +
  qualified-suffix symbol-definition matcher extracted from
  `mcp/server.py:_match_symbol` (see D-023's original docstring for the
  exact semantics to preserve), plus an ambiguity check: how many
  *distinct* definitions across the whole repo share a given bare
  trailing name — this is what lets `analyze_impact` label a caller
  `CONFIRMED` vs `LIKELY` (see Rules).
- `src/codebase_rag_mcp/impact/models.py` — `Confidence` (StrEnum:
  `CONFIRMED`, `LIKELY`), `CallerInfo` (file, line,
  `caller_symbol: str | None`, confidence, is_likely_test),
  `ImporterInfo` (file, line, confidence), `ImpactResult` (symbol,
  definitions: list[SearchHit], callers, importers,
  `callers_truncated: bool`, `importers_truncated: bool`,
  explanation: str | None, has_evidence: bool) — `mcp/server.py`'s
  `analyze_impact` tool returns `impact.models.ImpactResult` directly,
  mirroring how `ask` returns `generation.models.GeneratedAnswer`
  directly rather than a wrapper (D-023's own precedent).
  `CallerInfo.caller_symbol` is the containing symbol's **base name with
  any `#partN` oversized-chunk suffix stripped** (e.g. a call inside
  `_build_server#part2` reports `caller_symbol="_build_server"`, never
  the raw chunk-id suffix — that suffix is an internal chunk-storage
  artifact, not something a caller of this tool should ever see).
  `caller_symbol` is `None` when the reference line falls inside a
  whole-file-fallback chunk (`symbol=""`, `type=SymbolKind.MODULE`) —
  i.e. a real "this call happens in this file, but there's no enclosing
  symbol to name" case, represented explicitly rather than as a
  misleading empty string.
- `src/codebase_rag_mcp/impact/analyzer.py` — `analyze_impact(symbol,
  chunks, reference_index) -> ImpactResult`: the deterministic-evidence
  assembly. Finds definition(s) via `impact.symbols`; for the bare
  trailing name, looks up `CALL` references in `reference_index` and
  labels each `CONFIRMED` only when the bare name is unambiguous
  repo-wide, else `LIKELY`; resolves each caller's containing symbol by
  finding which chunk's `[start_line, end_line]` the reference line falls
  inside, **stripping any `#partN` suffix from the reported
  `caller_symbol` and reporting `None` instead of `""` for a
  whole-file-fallback containing chunk** (see `impact/models.py` above —
  this is the concrete fix for the interaction between Day 1's
  oversized-symbol/whole-file fallbacks and this day's containing-symbol
  resolution); flags `is_likely_test` via a **path-segment/filename
  heuristic, not a raw substring match** — a path component exactly
  equal to (or matching) `test`/`tests`, or a filename matching
  `test_*.py` / `*_test.py` / `*.test.ts` / `*.spec.ts`
  (case-insensitive) — deliberately narrow enough that `contest.py` or
  `latest_config.py` are never misflagged, no test execution involved;
  looks up `IMPORT` references whose module path resolves to the
  defining file, each labeled `LIKELY` (import resolution here is
  file-level, not "is this specific name actually used" — a named,
  accepted simplification, same spirit as `find_symbol`'s
  name-based-not-type-resolved matching). Import resolution tries a
  **full repo-relative path match first**, and only falls back to a
  basename/suffix match if no full-path match is found — this reduces
  (but, per the accepted simplification, does not eliminate) false
  positives from same-named files in different packages or barrel-style
  re-exports; any resolution reached only via the basename fallback is
  still labeled `LIKELY` like all import evidence, with no separate
  confidence tier, since over-engineering the confidence granularity here
  isn't worth it for a V2 simplification that's already honestly labeled.
  Both `callers` and `importers` are capped at
  `MAX_IMPACT_REFERENCES_PER_KIND` (a module-level `Final[int]` in
  `impact/analyzer.py`, defaulting to 50 — same locally-scoped-constant
  convention `chunker/fallback.py`'s `DEFAULT_MAX_CHUNK_LINES` already
  set, not a new `config.py` entry) so a very common bare name (`run`,
  `close`, `get`) can never return an unbounded list; when the real count
  exceeds the cap, the corresponding `*_truncated` flag on `ImpactResult`
  is set `True` so the caller knows the list is partial rather than
  silently dropping evidence. Zero definitions → `has_evidence=False`, no
  LLM call, mirroring `generate_answer`'s zero-candidates short-circuit
  (D-022).
- `src/codebase_rag_mcp/impact/explain.py` — `explain_impact(symbol,
  result: ImpactResult) -> str`: called only when `result.has_evidence`
  is `True`. Reuses `generation.providers.registry.select_providers()`
  and the same extract-JSON-then-Pydantic-validate pattern
  `generation/pipeline.py` already established — the model is given only
  the deterministic `callers`/`importers`/`definitions` already collected
  (using the same cleaned, suffix-stripped `caller_symbol` values, never
  raw chunk ids) and is prompted to narrate them, never to invent new
  ones. To make fabrication mechanically checkable (not just
  prompt-requested), the structured output includes
  `referenced_files: list[str]` cross-checked against `result`'s real
  evidence files exactly like `citations.attach.attach_citations`
  cross-checks `cited_chunk_ids` — any file named in the narrative that
  isn't in the evidence is dropped/flagged, never surfaced as if it were
  verified. If `callers_truncated`/`importers_truncated` is `True`, the
  prompt explicitly tells the model the list is partial, so the
  narrative says "at least N callers" / "additional callers exist but
  aren't shown" rather than implying the returned list is exhaustive.
- `src/codebase_rag_mcp/impact/prompts.py` — the impact-explanation
  system/user prompt templates, mirroring `generation/prompts.py`'s
  existing pattern rather than inlining prompt strings in `explain.py`.
- `tests/test_indexing_references.py` — build/persist/reload round-trip
  for `indexing.references`, mirroring `tests/test_indexing_bm25.py`'s
  shape.
- `tests/test_impact.py` — `impact.symbols` ambiguity detection,
  `impact.analyzer.analyze_impact` (confirmed vs. likely labeling, a
  zero-definitions case, a test-caller flag case — including a
  false-positive-avoidance case for a path like `contest.py`/
  `latest_config.py` that must **not** be flagged `is_likely_test`), a
  case asserting `caller_symbol` has its `#partN` suffix stripped for a
  call site inside a known oversized/split symbol, a case asserting
  `caller_symbol is None` for a call site inside a whole-file-fallback
  chunk, and a case asserting `callers_truncated=True` with exactly
  `MAX_IMPACT_REFERENCES_PER_KIND` callers returned when a fixture has
  more callers than the cap. `impact.explain.explain_impact`
  (evidence-only narration, a fabricated-file rejection case using a
  mocked provider response, mirroring how `tests/test_generation.py`
  tests the anti-fabrication citation override, and a truncated-list
  case confirming the narrative doesn't imply completeness).
- Extend `tests/test_parser.py` — new reference-extraction fixtures for
  TS/JS and Python (known call sites + import statements → exact
  expected `(name, kind, line)` tuples).
- Extend `tests/test_mcp_server.py` — `analyze_impact` end-to-end via the
  same real `MCPServer`/`ClientSession` smoke-test approach D-023 used
  for Day 08's tools; confirm `find_symbol`'s existing tests still pass
  unmodified after the `_match_symbol` extraction.

## New dependencies
No new dependencies. Reference extraction reuses the already-installed
`tree-sitter`/`tree-sitter-language-pack` and the already-cached grammars
(`parser/grammars.py`); the LLM explanation step reuses the already-wired
provider SDKs from Day 07. No new free API key required.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting
  for code. (Unaffected by this day — no chunking changes — but this
  invariant still applies to anything this day touches in `chunker/`.)
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata — extend this to `CallerInfo`/`ImporterInfo`:
  every one must carry a real `file`/`line`, never an approximate or
  inferred location. `caller_symbol` is a display convenience derived
  from that real location (see `impact/models.py`/`impact/analyzer.py`
  above) — stripping the internal `#partN` suffix and using `None` for
  "no enclosing symbol" are about not leaking storage internals or
  faking precision, not about weakening the underlying file/line
  guarantee, which stays exact either way.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess —
  for this day, that means `analyze_impact` returns `has_evidence=False`
  and skips the LLM call entirely for a symbol with zero definitions
  found, and `explain_impact`'s output is mechanically checked against
  the real evidence list (see "Files to create" above), not merely
  prompt-requested to stay grounded. When evidence was capped
  (`*_truncated=True`), the LLM must be told so and must not narrate the
  capped list as if it were complete.
- All LLM calls go through the provider fallback chain defined in
  CLAUDE.md/`generation.providers.registry` — never hardcode a single
  provider or build a second, separate provider-selection mechanism for
  this feature.
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose — `explain_impact`'s provider call is validated against a
  Pydantic schema (narrative + `referenced_files`) the same way
  `generation/pipeline.py`'s `_LLMStructuredOutput` is, not parsed as
  raw text.
- Embedding and reranker models run locally by default — unaffected by
  this day (no embedding/reranker use in the reference index or impact
  analysis at all).
- **Name-based matching, not type/scope resolution, is an explicitly
  accepted V2 simplification** — a call site named `pause` cannot be
  proven to call a specific class's `pause` method without real
  type-checking, which is out of scope (CLAUDE.md: "Perfect
  whole-program static analysis" is explicitly *not* being built). The
  `CONFIRMED`-vs-`LIKELY` labeling exists precisely to make this
  limitation visible to the caller instead of silently overclaiming
  precision — never remove or weaken this labeling to make output look
  more confident than the underlying evidence supports. The same
  full-path-before-basename preference used for import resolution is a
  precision improvement *within* this simplification, not an attempt to
  escape it — a basename-only import match is still `LIKELY`, never
  promoted to `CONFIRMED`.
- Any deterministic-evidence list (`callers`, `importers`) that is capped
  for size must say so via its `*_truncated` flag — never silently drop
  evidence and present a partial list as if it were exhaustive. This
  applies to both the raw `ImpactResult` and anything `explain_impact`
  narrates from it.
- The `is_likely_test` heuristic must match on path segments or filename
  patterns, never a bare substring search over the full path — this is
  what keeps `contest.py`/`latest_config.py` from being misflagged, and
  should be treated with the same care as any other heuristic that's
  cheap to get subtly wrong.
- Do not duplicate `_match_symbol`'s exact/qualified-suffix matching
  logic a second time in `impact/`; extract it once into
  `impact/symbols.py` and have both `find_symbol` and `analyze_impact`
  call the same function.
- Log every meaningful decision to `DECISIONS.md` — in particular, the
  final call-node/import-node Tree-sitter node types used per language
  (these are easy to get subtly wrong and worth recording precisely, the
  same way D-013 recorded the `ClassName.method` qualification rule), the
  `#partN`-suffix-stripping/`None`-for-whole-file-fallback convention for
  `caller_symbol`, the full-path-before-basename import resolution order,
  the path-segment `is_likely_test` heuristic, and the
  `MAX_IMPACT_REFERENCES_PER_KIND` cap value and truncation-flag
  convention.
- Update `FLOW.md` with the new reference-collection path (extending
  section 2's offline build flow diagram) and the new `analyze_impact`
  sequence (extending section 3's online query flow), following the
  existing Mermaid diagram conventions already in that file.

## Definition of done
- [ ] `parse_file` on real TS/JS and Python fixtures returns the exact
      expected `ParseResult.references` (name, kind, line) for known call
      sites and import statements, verified by `tests/test_parser.py`.
- [ ] `indexing.repo.build_all_indexes` run against the real demo repo
      persists `references.json`; a fresh process can
      `indexing.references.load_index(index_dir)` and get back the same
      reference count with no rebuild (`tests/test_indexing_references.py`).
- [ ] `impact.analyzer.analyze_impact` against a fixture with one
      unambiguous symbol name returns its caller labeled `CONFIRMED`; a
      fixture with two different classes sharing a method name returns
      `LIKELY` for calls to that name (`tests/test_impact.py`).
- [ ] A call site inside a real oversized/split symbol (e.g. this repo's
      own `_build_server`, already confirmed split into `#part1`/`#part2`
      by the Day 1–9 fallback verification) resolves to
      `caller_symbol="_build_server"`, never the raw `#partN`-suffixed
      chunk name.
- [ ] A call site inside a whole-file-fallback chunk resolves to
      `caller_symbol=None`, not `""` or a misleading placeholder.
- [ ] The `is_likely_test` heuristic correctly flags `test_foo.py`,
      `foo_test.py`, and a file under a `tests/` directory segment, and
      correctly does **not** flag `contest.py` or `latest_config.py`
      (`tests/test_impact.py`).
- [ ] A fixture with more callers (or importers) than
      `MAX_IMPACT_REFERENCES_PER_KIND` returns exactly the capped count
      with the matching `*_truncated` flag set `True`; a fixture at or
      under the cap returns `*_truncated=False`.
- [ ] `analyze_impact("some_symbol_with_zero_definitions", ...)` returns
      `has_evidence=False` and no provider is ever called (assert via a
      mock/spy on `select_providers`/`provider.complete`, mirroring
      `test_generation.py`'s zero-candidates test).
- [ ] `impact.explain.explain_impact`, given a mocked provider response
      that names a file absent from the real evidence, does not surface
      that fabricated file in the result; given a truncated evidence set,
      the narrative does not claim the list is exhaustive
      (`tests/test_impact.py`).
- [ ] The new `analyze_impact` MCP tool is registered and callable
      end-to-end (real `MCPServer` + `ClientSession`, small fixture repo)
      returning a well-formed `ImpactResult`, verified in
      `tests/test_mcp_server.py`.
- [ ] `find_symbol`'s full existing test suite in `tests/test_mcp_server.py`
      still passes unmodified after the `_match_symbol` →
      `impact/symbols.py` extraction (regression guard on the refactor).
- [ ] Manually run `analyze_impact` against a real function in the
      currently-indexed demo repo and confirm the returned
      callers/importers/line numbers are actually correct against the
      real source (not just internally consistent) — including at least
      one import-resolution check against any same-basename-in-different-
      package or re-export/barrel case the demo repo happens to contain,
      specifically to sanity-check the full-path-before-basename fallback
      ordering rather than assuming it's fine.
- [ ] `DECISIONS.md` has a new entry for this day (Tree-sitter node types
      chosen per language, the confirmed-vs-likely labeling rule, the
      lenient-absence handling for a missing reference index, the
      `caller_symbol` suffix-stripping/`None` convention, the
      full-path-before-basename import resolution order, the
      path-segment `is_likely_test` heuristic, and the
      `MAX_IMPACT_REFERENCES_PER_KIND` truncation convention) and
      `FLOW.md`'s build/query diagrams are updated to include the new
      reference-collection and `analyze_impact` paths.
- [ ] `mypy`/`ruff` clean on all new and changed files (matching the
      repo's existing tooling gates).