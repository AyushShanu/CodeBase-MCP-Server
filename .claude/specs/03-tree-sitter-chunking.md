# Spec: Tree-sitter Parsing & AST Chunking

## Overview
Day 02 (github-ingestion) produces a filtered `RepoStats`/`FileRecord` list —
every included file's repo-relative path, detected language, and size — but
no code has been read or understood yet. This day adds the first stage that
actually looks inside files: parse each included source file into a
Tree-sitter AST, walk that AST to extract top-level structural units
(functions, classes, methods, interfaces), and turn those units into
retrievable "chunks" carrying exact file/line metadata. This is the
foundation every later stage depends on — embeddings (Day 04), BM25 (Day 05),
hybrid retrieval, reranking, and every MCP tool all operate on the chunk
list this stage produces, never on raw file text. Per CLAUDE.md's roadmap,
this day scopes to TypeScript/JavaScript first, with Python parsing folded
in only if time allows.

**Out of scope this day, by design:** there is no whole-repository
orchestration function here (nothing that loops over `RepoStats.files`,
opens each included file, and aggregates chunks across the whole repo).
Every function in this spec (`parse_file`, `chunk_file`) operates on one
file at a time, exercised directly by tests and by a per-file manual smoke
test. Repo-wide aggregation is expected to land in Day 04, which is the
first stage that actually needs "all chunks for the repo" as a single
collection (to embed them). Day 04's spec should account for adding that
glue itself rather than assuming it already exists.

## Depends on
- Day 01 — Foundation (package scaffold, config, CLI, MCP stub). Complete.
- Day 02 — GitHub Ingestion & File Filtering (`ingestion.loader`,
  `ingestion.scanner`, `ingestion.models.FileRecord`/`RepoStats`). Complete
  per `.claude/specs/02-github-ingestion.md` and DECISIONS.md D-009 — this
  day consumes its `FileRecord` list (`path`, `language`, `included`) as
  input; it does not touch ingestion itself.

## Pipeline stage(s) touched
- **Parsing & Chunking** (new logic — both `parser/` and `chunker/` are
  currently placeholder `__init__.py` stubs per D-007).

No other stage (Ingestion, Embedding, BM25, Hybrid Retrieval, Reranking,
Generation, Citations, MCP Server, Impact Analysis) is touched.

## MCP tools affected
No MCP tool changes. `search_code`, `find_symbol`, `get_file_context`,
`analyze_impact`, and `repository_summary` all land in Day 08+ and will
consume this stage's chunk output later, but nothing MCP-facing exists yet.

## Model / provider choice for this step
Not applicable in the CLAUDE.md stack-table sense — this day has no LLM,
embedding, reranker, or vector-store component.

One local (non-table) decision this spec makes and must be logged to
DECISIONS.md when implemented: **which Tree-sitter grammar-loading
mechanism to use.**
- **Primary:** `tree-sitter-language-pack` (already a declared dependency
  in `pyproject.toml` — see D-004's neighbor deps) via its
  `get_language(name)` / `get_parser(name)` helpers. One package gives
  every grammar needed (TypeScript, TSX, JavaScript, and Python later)
  without per-language pip packages or manual grammar compilation.
- **Documented alternative:** individual per-language packages
  (`tree-sitter-typescript`, `tree-sitter-javascript`,
  `tree-sitter-python`, ...) — more explicit pinning per grammar, but N
  extra dependencies for N languages and manual `Language(...)` wiring
  for each. Rejected: no benefit at this scale, and
  `tree-sitter-language-pack` is already the declared dependency.

## Files to change
- `src/codebase_rag_mcp/parser/__init__.py` — replace the placeholder
  docstring-only module with real exports (`parse_file`, `ParsedSymbol`,
  `ParseResult`, `SymbolKind`).
- `src/codebase_rag_mcp/chunker/__init__.py` — replace the placeholder
  docstring-only module with real exports (`chunk_file`, `Chunk`).

## Files to create
- `src/codebase_rag_mcp/parser/models.py` — Pydantic models:
  - `SymbolKind` (StrEnum): `function`, `method`, `class`, `interface`,
    `module`, `unknown`. **`module` is a first-class member, not a
    separate "fallback kind"** — it's what `chunker.chunk_file` assigns to
    the whole-file fallback chunk described below, so the type system has
    exactly one enum for "what kind of thing is this chunk" everywhere.
  - `ParsedSymbol`: `name` (for a class-nested symbol, this is the
    **qualified** name — `f"{class_name}.{method_name}"`, e.g.
    `"Button.render"` — never a bare method name; two different classes
    with a same-named method must not produce colliding symbol
    identifiers), `kind`, `start_line`, `end_line` (1-indexed — **Tree-sitter
    node positions are 0-indexed internally**, so `extractor.py` must add 1
    when converting a node's row to `start_line`/`end_line`; this is the
    single most common off-by-one bug when wiring up Tree-sitter for the
    first time, call it out in code with a comment, not just get it right
    by luck), `start_byte`, `end_byte`.
  - `ParseResult` (path, language, symbols: list[ParsedSymbol],
    parse_errors: list[str]).
- `src/codebase_rag_mcp/parser/exceptions.py` — typed errors:
  `UnsupportedLanguageError` (language has no configured grammar/query —
  never silently returns empty), `ParseError` (Tree-sitter failed to
  produce a usable tree).
- `src/codebase_rag_mcp/parser/grammars.py` — language-name (from
  `ingestion.languages`, e.g. `"typescript"`, `"javascript"`, `"python"`)
  to Tree-sitter grammar-pack name mapping, wrapping
  `tree_sitter_language_pack.get_parser`/`get_language` with a small
  cache (parsers are expensive to construct — do not rebuild per file).
- `src/codebase_rag_mcp/parser/extractor.py` — `parse_file(path: Path,
  language: str, source: bytes) -> ParseResult`: runs the Tree-sitter
  parser on the raw bytes (byte offsets are the authoritative position
  data Tree-sitter works in — no decoding happens in this module), then
  walks/queries the tree for top-level and class-nested
  function/class/method/interface declarations per language:
  - TS/JS query patterns: `function_declaration`, `class_declaration`,
    `method_definition`, `interface_declaration`, arrow functions assigned
    to a `const`.
  - **Export-wrapped declarations must be captured identically to
    unwrapped ones.** `export function foo() {}`, `export default class
    Foo {}`, and `export const handler = (req, res) => {}` all still
    contain a plain `function_declaration`/`class_declaration`/arrow-const
    node — the query must match the node type wherever it appears in the
    tree, not assume only direct children of the root count as
    "top-level." Since essentially all real-world TS/JS uses ES module
    exports, this is not an edge case, it's the common case.
  - **Anonymous default exports get a fixed name, not silent exclusion.**
    `export default (req, res) => {}` or `export default function () {}`
    has no bound identifier — extract it anyway with `name = "default"`,
    documented and tested, rather than leaving the behavior undefined.
  - Python folded in only if time allows, per roadmap, using
    `function_definition`/`class_definition`.
  - Tree-sitter parse errors (`tree.root_node.has_error`) are captured in
    `ParseResult.parse_errors`, not raised — a single malformed file must
    never abort a whole-repo parse run.
- `src/codebase_rag_mcp/chunker/models.py` — Pydantic model `Chunk`: `id`
  (deterministic — derived from path + symbol name + start_line, stable
  across re-runs for citation stability), `repo` (optional repo
  identifier, may be empty for local-path sources), `file` (repo-relative
  path, reusing `FileRecord.path`), `symbol` (the qualified name from
  `ParsedSymbol.name`, or `""` only for the whole-file fallback chunk),
  `type` (`SymbolKind` — including `SymbolKind.module` for the fallback
  case; no separate type needed), `language`, `start_line`, `end_line`,
  `content` (the exact source slice for these lines).
- `src/codebase_rag_mcp/chunker/chunker.py` — `chunk_file(parse_result:
  ParseResult, source: bytes, *, encoding: str = "utf-8") -> list[Chunk]`:
  **this is the single place file bytes become text in this pipeline** —
  decode `source` once, with `errors="replace"` so a file containing
  invalid byte sequences (a stray non-UTF-8 file that slipped past
  ingestion's extension-based filters, or a BOM) never crashes chunking;
  the replacement character doesn't insert or remove newlines, so
  line-splitting afterward stays correct. Split the decoded text into
  lines once, then build one chunk per extracted `ParsedSymbol`, content
  sliced by its exact `[start_line, end_line]` range. Files with zero
  extracted symbols (e.g. plain scripts, config-like source, or languages
  without a query yet) still get a whole-file fallback `Chunk` with
  `type=SymbolKind.module` and `symbol=""` rather than being silently
  dropped, so nothing indexed later has a gap.
- `src/codebase_rag_mcp/chunker/fallback.py` — safe fallback splitting for
  oversized symbols: when a single `ParsedSymbol`'s line span exceeds a
  configurable `MAX_CHUNK_LINES` budget, split it into multiple chunks
  along line boundaries *within* that symbol's span (never splitting
  another symbol's boundary, never falling back to raw character-count
  splitting of arbitrary file content) and suffix each resulting chunk's
  `symbol` field (e.g. `"handleRequest#part2"`, or `"Button.render#part2"`
  for a qualified nested symbol) so citations stay meaningful.
- `tests/test_parser.py` — unit tests (see Definition of Done).
- `tests/test_chunker.py` — unit tests (see Definition of Done).

## New dependencies
No new dependencies. `tree-sitter>=0.23` and `tree-sitter-language-pack>=0.6`
are already declared in `pyproject.toml`'s `dependencies` (Day 01 scaffold)
and mypy already has an override entry for both — this day is the first to
actually import and use them. No new API key required; this is a fully
local, offline parsing step.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. The only permitted exception is `chunker/fallback.py`'s
  within-symbol line-boundary splitting for oversized functions, which
  still respects the enclosing symbol's boundaries and must not degrade
  into naive whole-file character slicing.
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata. Every `Chunk` must have a non-empty `file`,
  correct 1-indexed `start_line`/`end_line`, and `content` that actually
  matches those lines in the source file — add a test that asserts this
  invariant directly (slice the raw file yourself in the test and compare).
- **Tree-sitter node positions are 0-indexed internally.** `extractor.py`
  must convert to 1-indexed line numbers before constructing any
  `ParsedSymbol` — test this directly against a symbol starting on the
  file's literal first line (`start_line == 1`, not `0`).
- **Export-wrapped declarations must be extracted identically to
  unwrapped ones.** Add explicit fixture coverage for `export function`,
  `export default class`, and `export const x = () => {}` — do not assume
  only bare top-level declarations need querying.
- **Class-nested symbols (methods) get qualified names**
  (`ClassName.methodName`), never bare method names, so two classes
  sharing a method name don't produce colliding symbol identifiers or
  ambiguous results in a later `find_symbol` MCP tool.
- **Anonymous default-export functions/arrow expressions with no bound
  identifier are named `"default"`** — an explicit, documented choice,
  not silently dropped and not a crash.
- **File bytes are decoded to text exactly once, in `chunk_file`**, using
  a fixed encoding (`utf-8`) with `errors="replace"`. Never let a decode
  failure crash a whole-repo run, and never decode the same file twice
  with different policies in different modules — `parse_file` stays in
  bytes throughout, `chunk_file` is the only text boundary.
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess.
  *(Not applicable this day — no LLM calls in parsing/chunking.)*
- All LLM calls go through the provider fallback chain defined in
  CLAUDE.md — never hardcode a single provider. *(Not applicable this
  day.)*
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose. Parsing/chunking has no LLM calls, but `ParsedSymbol`,
  `ParseResult`, and `Chunk` must still be Pydantic models for consistency
  with `ingestion.models` and so Day 04/05 can consume them with free
  validation.
- Embedding and reranker models run locally by default — no paid API in
  the default path. *(Not applicable this day.)*
- A single file that fails to parse (syntax error, unsupported grammar
  edge case, truncated/binary-ish content that slipped past ingestion's
  filters) must never crash a whole-repo run — catch and record the error
  on that file's `ParseResult.parse_errors`, skip chunk extraction for the
  broken symbol(s), and continue to the next file.
- Do not silently return an empty chunk list for a file whose language has
  no configured Tree-sitter query yet — raise `UnsupportedLanguageError`
  (caller decides whether to skip or fall back to a whole-file chunk) so
  gaps in language coverage are visible, not silently swallowed.
- Log every meaningful decision to `DECISIONS.md` (in particular: the
  `tree-sitter-language-pack` choice above, the `MAX_CHUNK_LINES`
  fallback-splitting threshold chosen and why, and the qualified-symbol-
  naming convention for nested methods).
- Update `FLOW.md`'s "Offline build flow" section (the `C[parser<br/>
  tree-sitter AST]` / `D[chunker<br/>AST-aware splits]` boxes already
  exist as placeholders in the Mermaid diagram) to describe the actual
  module call chain (`parser.extractor.parse_file` →
  `chunker.chunker.chunk_file` → `chunker.fallback` for oversized symbols)
  instead of the current placeholder boxes. Note explicitly in the prose
  above the diagram that repo-wide orchestration (looping over all of
  Day 02's `FileRecord`s) is deferred to Day 04.

## Definition of done
- [ ] `parse_file(path, "typescript", source)` on a `.ts` fixture
      containing a function, a class with methods, and an interface
      returns a `ParseResult` whose `symbols` list has one `ParsedSymbol`
      per declaration with correct `kind` and 1-indexed line ranges.
- [ ] Same coverage for `.tsx` and `.js`/`.jsx` fixtures (arrow-function-
      as-const counted as `function` kind).
- [ ] A fixture using `export function`, `export default class`, and
      `export const x = () => {}` is parsed and each produces a symbol
      identical in kind/line-range correctness to its unwrapped
      equivalent — export wrapping must not hide or alter extraction.
- [ ] A fixture with an anonymous default export
      (`export default (req, res) => {}`) is extracted with
      `symbol == "default"`, not silently dropped and not raising.
- [ ] A symbol starting on the file's literal first line reports
      `start_line == 1`, not `0` — direct regression test for the
      Tree-sitter 0-indexed-to-1-indexed conversion.
- [ ] A fixture with two different classes that each define a same-named
      method (e.g. both have `render()`) produces two `ParsedSymbol`s
      with distinct, qualified names (`"A.render"`, `"B.render"`), never
      colliding bare names.
- [ ] A fixture file with a deliberate syntax error is parsed without
      raising: `ParseResult.parse_errors` is non-empty, and any symbols
      that *can* still be extracted are still returned.
- [ ] `parse_file` on a language with no configured query (e.g. a
      `.rb`/ruby fixture) raises `UnsupportedLanguageError`, not a silent
      empty result.
- [ ] `chunk_file` on a parsed TS/JS fixture returns one `Chunk` per
      extracted symbol, and each `Chunk.content` exactly matches the raw
      file sliced by `[start_line, end_line]` (assert this by independently
      slicing the fixture file in the test, not by trusting the chunker).
- [ ] `chunk_file` on a file with zero extracted symbols (e.g. a flat
      constants file) returns a single whole-file fallback `Chunk` with
      `type == SymbolKind.module` and `symbol == ""`, rather than an
      empty list or an undefined type.
- [ ] `chunk_file` given bytes containing an invalid UTF-8 sequence (or a
      UTF-8 BOM) decodes without raising (`errors="replace"`) and still
      produces chunks for the rest of the file's content.
- [ ] A fixture containing one deliberately oversized function (line count
      > `MAX_CHUNK_LINES`) is split into multiple `Chunk`s by
      `chunker/fallback.py`, each still within that function's line span
      (none extend before its `start_line` or after its `end_line`), and
      each chunk's `symbol` field is distinguishable (e.g. `#part1`,
      `#part2`) and still carries the qualified base name for nested
      symbols.
- [ ] `Chunk.id` is deterministic: parsing and chunking the same fixture
      file twice produces identical chunk IDs in the same order.
- [ ] `pytest` suite in `tests/test_parser.py` and `tests/test_chunker.py`
      covers all of the above on small fixture files (fixtures may live
      inline in the test file or under a `tests/fixtures/` directory) —
      all passing.
- [ ] `ruff check` and `mypy` pass clean on all new/changed files.
- [ ] Manual smoke test: run `parse_file` + `chunk_file` against a handful
      of real files from a small public TS/JS GitHub repo (or a local
      clone) end-to-end from a Python shell/script, and confirm the
      resulting chunks' file/line/content look sane by eye — no MCP tool
      or CLI wiring exists yet to drive this, so this stays a direct-call
      smoke test.
- [ ] `DECISIONS.md` has a new dated entry for the
      `tree-sitter-language-pack` grammar-loading choice, the
      `MAX_CHUNK_LINES` fallback threshold, and the qualified-symbol-name
      convention for nested methods.
- [ ] `FLOW.md`'s "Offline build flow" section is updated to describe the
      real parser → chunker call chain instead of the current placeholder
      boxes, and its prose notes that repo-wide orchestration is deferred
      to Day 04.