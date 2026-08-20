# Spec: GitHub Ingestion & File Filtering

## Overview
This is the first pipeline-logic day of the build (Day 01 only scaffolded the
package, config, CLI, and MCP stub with no real behavior). Ingestion is the
entry point for the entire RAG pipeline: every later stage — Tree-sitter
parsing, chunking, embedding, BM25, retrieval, and the `get_file_context` /
`repository_summary` MCP tools — operates on the file list this stage
produces. This day adds the ability to point the tool at a GitHub repo URL
or a local path, safely obtain a working copy, filter out noise
(`.git`, `node_modules`, build artifacts, binaries, lockfiles, oversized
files), detect languages by extension, and return a structured summary of
what was ingested. Nothing downstream (parsing/chunking/embedding) exists
yet, so this stage's output is consumed only by tests and a manual smoke
check for now.

Because this stage clones and walks arbitrary third-party repositories, it
is also the pipeline's main untrusted-input boundary — treat every cloned
file and path as hostile input, not just as data.

## Depends on
- Day 01 — Foundation (package scaffold, `pyproject.toml`, `config.py`,
  CLI entrypoint, MCP stdio stub). Must be complete, and it is (see
  DECISIONS.md D-001–D-008).

## Pipeline stage(s) touched
- **Ingestion** (new logic — currently an empty placeholder module).

No other stage (Parsing & Chunking, Embedding, BM25, Hybrid Retrieval,
Reranking, Generation, Citations, MCP Server, Impact Analysis) is touched.

## MCP tools affected
No MCP tool changes. `search_code`, `find_symbol`, `get_file_context`,
`analyze_impact`, and `repository_summary` are all unaffected today — they
land in Day 08+ and will consume ingestion's output later.

## Model / provider choice for this step
Not applicable. Ingestion is a pure file I/O / filtering layer with no LLM,
embedding, reranker, or vector-store component, so none of CLAUDE.md's
stack-table choices are implicated this day.

One local (non-table) decision this spec makes and must be logged to
DECISIONS.md when implemented: **how to obtain a working copy of a GitHub
repo.**
- **Primary:** shell out to the system `git` binary via `subprocess`
  (`git clone --depth 1 <url> <dest>`, called with an argument list, never
  `shell=True`) — zero new pip dependency, and shallow clone keeps it fast
  and avoids pulling full history.
- **Documented alternative:** `GitPython` — nicer Python API, but it's an
  extra dependency wrapping the same underlying `git` binary, and adds
  little value for a single shallow-clone call.

Do not silently pick between these two without noting the choice (and the
alternative) in DECISIONS.md, per CLAUDE.md convention.

## Files to change
- `src/codebase_rag_mcp/ingestion/__init__.py` — replace the placeholder
  docstring-only module with real exports (`ingest`, `RepoSource`,
  `RepoStats`, `FileRecord`).
- `pyproject.toml` — only if the GitPython alternative is chosen instead of
  the `subprocess` + system-`git` primary approach (adds `GitPython` to
  `dependencies`). Not needed if the primary approach is used.

## Files to create
- `src/codebase_rag_mcp/ingestion/models.py` — Pydantic models:
  `RepoSource` (github URL or local path, normalized), `FileRecord` (path,
  language, size_bytes, included/excluded + reason), `RepoStats` (totals,
  per-language breakdown, excluded-file breakdown by reason).
- `src/codebase_rag_mcp/ingestion/loader.py` — `load_repo(source: str) ->
  RepoSource`: detects whether `source` is a GitHub URL (`https://` only —
  reject `ssh://`, `git://`, `ext::`, `file://`, or any non-`https` scheme)
  or a local path; for a URL, shallow-clones into a temp/`DATA_DIR`
  subdirectory using `subprocess` + the system `git`; for a local path,
  validates it exists and is a directory. Enforces a clone timeout
  (`subprocess.run(..., timeout=...)`) that raises a clear, typed error
  (e.g. `RepoCloneTimeoutError`) rather than hanging.
- `src/codebase_rag_mcp/ingestion/filters.py` — ignore rules: directory
  names (`.git`, `node_modules`, `build`, `dist`, `coverage`, `.venv`,
  `__pycache__`, etc.), binary/lockfile patterns
  (`*.lock`, `package-lock.json`, `poetry.lock`, `*.png`, `*.jpg`, `*.woff`,
  `*.zip`, `*.pdf`, etc.), and a `MAX_FILE_SIZE_BYTES` size cap (configurable,
  sane default e.g. 1 MB) — each rule reports *why* a file was excluded.
- `src/codebase_rag_mcp/ingestion/languages.py` — static extension → language
  name mapping (`.py` → python, `.ts`/`.tsx` → typescript, `.js`/`.jsx` →
  javascript, `.go`, `.rs`, `.java`, etc.) with an `unknown` fallback.
  Extension-based only — no parsing/Tree-sitter dependency yet (that's
  Day 03).
- `src/codebase_rag_mcp/ingestion/scanner.py` — `scan(root: Path) ->
  RepoStats` walking the filtered tree, applying `filters.py` +
  `languages.py`, and building the final `FileRecord` list + `RepoStats`.
  **Must not follow symlinks that resolve outside `root`** — see security
  rule below; treat every symlink as suspect, not just oversized/binary
  files.
- `tests/test_ingestion.py` — unit tests (see Definition of Done).

## New dependencies
No new dependencies if the `subprocess` + system-`git` primary approach is
used (recommended — no new pip package, no new API key). If the GitPython
alternative is chosen instead, add `GitPython>=3.1` to `pyproject.toml`
`dependencies` — still no API key required either way, this is a local
filesystem/git operation only.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for
  code. *(Not directly exercised this day — no chunking yet — but do not
  add any pre-chunking logic here that later code would have to work
  around.)*
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata. *(Not directly exercised this day — no chunks
  exist yet — but `FileRecord.path` must be a clean, repo-relative path so
  future line-metadata attachment in Day 03 has a stable anchor.)*
- LLM answers must rely only on retrieved evidence; implement an explicit
  "not enough evidence" fallback rather than letting the model guess.
  *(Not applicable this day — no LLM calls in ingestion.)*
- All LLM calls go through the provider fallback chain defined in
  CLAUDE.md — never hardcode a single provider. *(Not applicable this
  day.)*
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose. Ingestion has no LLM calls, but its own output types
  (`RepoSource`, `FileRecord`, `RepoStats`) must still be Pydantic models
  for consistency with this convention and to keep them easy for later
  stages to consume/validate.
- Embedding and reranker models run locally by default — no paid API in
  the default path. *(Not applicable this day.)*
- Only accept `https://` GitHub URLs for cloning — reject other schemes
  (`ssh://`, `git://`, `ext::`, `file://`, etc.) before invoking `git`.
  This is **not primarily a shell/command-injection concern** — `git` is
  invoked via `subprocess` with an argument list, never `shell=True`, so
  classic shell injection isn't the threat model. The real risks are
  SSRF/protocol abuse (git's `ssh://` and `ext::` transports can reach
  internal hosts or leak credentials via `.netrc`/an active SSH agent) and
  unbounded resource use from a hostile or oversized remote — restrict to
  `https://` and enforce a clone timeout to close both.
- **Never follow symlinks that resolve outside the cloned repository root
  during scanning.** A cloned repo can contain a symlink pointing anywhere
  on the host filesystem (`/etc/passwd`, a home directory, etc.); a naive
  file walk that follows symlinks would read and could later embed content
  from outside the intended checkout. `scanner.py` must either skip
  symlinked entries entirely by default, or resolve each symlink and
  verify the resolved path still lives inside the checkout root before
  including it — reject/exclude (with a logged reason) anything that
  escapes.
- Never execute code found inside a cloned repository as part of
  ingestion (no `setup.py`, no install hooks, no notebook execution).
- Enforce a clone timeout and do not follow redirects to non-`https`
  targets. This must be verified by a test, not just implemented — see
  Definition of Done.
- Log every meaningful decision to `DECISIONS.md` (in particular: the
  clone-mechanism choice above, using the SSRF/resource-exhaustion
  reasoning, not a command-injection framing).
- Update `FLOW.md`'s ingestion section to describe the ingestion step
  concretely (loader → filters/languages → scanner → stats) instead of the
  current placeholder box. **Verify the actual current heading in your
  `FLOW.md` first** — if it isn't already called "Offline build flow" (or
  no ingestion section exists yet), add one under whatever heading
  structure the file currently uses rather than assuming that name.

## Definition of done
- [ ] `load_repo("https://github.com/<owner>/<repo>")` shallow-clones the
      repo into a working directory and returns a valid `RepoSource`.
- [ ] `load_repo("/some/local/path")` validates and returns a `RepoSource`
      for a local path without invoking `git` clone.
- [ ] `load_repo` rejects a non-`https` URL (e.g. `git://...`, `ssh://...`,
      `ext::...`) with a clear, typed error before any subprocess call is
      made.
- [ ] Clone timeout is enforced and tested: a forced/mocked slow or
      unreachable clone causes `load_repo` to raise a clear, typed error
      (e.g. `RepoCloneTimeoutError`) within the configured timeout, rather
      than hanging indefinitely.
- [ ] `scan()` excludes `.git/`, `node_modules/`, `build/`, `dist/`,
      `coverage/`, `.venv/`, `__pycache__/` directories entirely.
- [ ] `scan()` excludes lockfiles (`package-lock.json`, `poetry.lock`,
      `*.lock`) and common binary extensions (images, fonts, archives, PDFs).
- [ ] `scan()` excludes files above `MAX_FILE_SIZE_BYTES` and records them
      in `RepoStats` under an "oversized" exclusion reason (not silently
      dropped).
- [ ] A fixture tree containing a symlink that resolves **outside** the
      checkout root is excluded by `scan()` (not followed/read), and this
      is covered by a dedicated test. A symlink that resolves **inside**
      the checkout root may be included, but only after resolution is
      verified.
- [ ] `RepoStats` reports total files seen, included count, excluded count
      broken down by reason (including the new symlink-escape reason), and
      a per-language breakdown of included files.
- [ ] Each included `FileRecord` has a repo-relative `path` and a detected
      `language` (or `"unknown"`).
- [ ] `pytest` suite in `tests/test_ingestion.py` covers: local-path
      ingestion end-to-end, URL-scheme rejection, clone-timeout
      enforcement, symlink-escape exclusion, each filter category
      (dir-name, lockfile, binary extension, oversized), and `RepoStats`
      correctness on a small fixture tree — all passing.
- [ ] `ruff check` and `mypy` pass clean on all new/changed files.
- [ ] Manual smoke test: run ingestion against a small real public GitHub
      repo (or a local clone of one) end-to-end from a Python shell/script
      and confirm the printed `RepoStats` look sane (no MCP tool exists yet
      to drive this, so this is a direct-call smoke test, not a CLI/MCP
      one).
- [ ] `DECISIONS.md` has a new dated entry for the clone-mechanism choice,
      including the corrected SSRF/resource-exhaustion reasoning (not a
      command-injection framing) for the `https://`-only restriction.
- [ ] `FLOW.md`'s ingestion section is updated to reflect the real module
      call chain (`loader.py` → `filters.py`/`languages.py` →
      `scanner.py`), under whichever heading actually exists in the file.