# Spec: Zero-Config Auto-Indexing on First Connect

## Overview
This build adds zero-config auto-indexing: when an MCP client (Claude Code/Claude
Desktop) launches `codebase-rag serve` and no usable index exists yet under
`index_dir` for the target repo, the server automatically builds one — using the
exact same incremental pipeline the CLI `index` command already uses — **without
ever blocking the MCP connection itself** on that build. Today, `mcp/server.py`'s
`_lifespan` raises `IndexNotAvailableError` immediately if neither the vector nor
the BM25 index is found, which means every new user must first run
`codebase-rag index <repo>` by hand, read its output, and only then add the server
to their MCP client config. That manual step is exactly the friction this feature
removes: point an MCP client at a repo (or just launch `serve` with the repo as
`cwd`, the common case for Claude Code) and connecting is enough — tool calls made
before the build finishes get a clear "still building" response instead of hanging
or returning misleading empty results.

**Roadmap note (flagging, not resolving silently):** CLAUDE.md's own Day 12 slot
is titled "Final QA & Open-Source Packaging," not this feature — and the
roadmap's `[ ]`/`[x]` checkboxes are already stale relative to real repo history
(`git log` shows Days 02–11 all merged via PRs #1–#9, with specs 02–11 present in
`.claude/specs/`, even though CLAUDE.md still shows most of them as `[ ]`). This
spec is filed at `day_number=12` per the explicit user request in the slash
command args, on top of that already-further-along actual state. Reconciling
CLAUDE.md's checkboxes/day-12 title is left to the user.

## Depends on
- **Day 08 (MCP Server V1)** — `mcp/server.py`'s `_lifespan` / `_build_server` /
  `run()` / `_ServerState`, which this day modifies directly.
- **Day 11 (V2 Polish)** — `indexing.repo.build_all_indexes_incremental` (the
  cache-aware build pipeline this day reuses verbatim, not a new pipeline) and
  `indexing.manifest` (used here to detect "the existing index belongs to a
  different repo than the one being served now").
- **Day 02 (GitHub Ingestion)** — `ingestion.loader.load_repo`, which already
  safely handles both `https://` URL cloning and local-path validation; auto-
  indexing reuses it unchanged, and — because cloning a remote repo can itself
  take an unpredictable amount of time — running it inside the same background
  task as the build (see Rules) is what keeps the remote-URL case from being a
  second, worse source of the exact blocking risk this day exists to remove.

## Pipeline stage(s) touched
- **MCP Server** (primary — startup/lifespan behavior, plus a small shared
  readiness guard added to all six existing tools).
- Ingestion / Embedding / BM25 are exercised indirectly through the existing
  `build_all_indexes_incremental` call; none of those stages themselves change.

## MCP tools affected
No tool **contract** changes (same request/response schemas). All six existing
tools (`search_code`, `find_symbol`, `get_file_context`, `ask`, `analyze_impact`,
`repository_summary`) gain one new behavior: each calls a shared
`_require_index_ready(state)` guard as its first line. If auto-indexing is still
running, that guard raises `IndexBuildInProgressError`; if it failed, it raises
`AutoIndexError` — both are ordinary MCP tool-call errors (same mechanism as an
existing `PathOutsideRepoRootError`/`IndexNotAvailableError`, surfaced to the
client as a clear tool error, never a hang and never a silent empty result), not
a schema change to any tool's success-path response model. This is a deliberate,
lower-blast-radius alternative to threading a new "status" field through six
different Pydantic response models — same guarantee (no crash-without-explanation,
no silent empty result), smaller diff, and consistent with how this codebase
already signals "can't proceed" conditions via typed exceptions rather than
sentinel fields.

## Model / provider choice for this step
Not applicable. This day does not touch the LLM, embedding, reranker, or
vector-store layer's selection logic — it reuses whatever `EMBEDDING_MODEL_NAME` /
`RERANKER_MODEL_NAME` / FAISS setup is already configured, exactly as
`codebase-rag index` does today. No new model/provider decision is introduced.

## Files to change
- `src/codebase_rag_mcp/mcp/server.py`:
  - `_ServerState` gains `indexing_status: Literal["ready", "in_progress",
    "failed"]` and `indexing_error: str | None` (frozen fields become mutable
    state here the same way `_ServerState` already holds live, swappable
    `vector_index`/`bm25_index`/etc. — see `_lifespan`'s existing pattern).
  - `_make_lifespan`/`_lifespan` gains the auto-index check. On the fast path
    (index already present and its manifest's `repo_root` matches the resolved
    effective source), behavior and latency are **completely unchanged** —
    `indexing_status="ready"` immediately, no build, no added delay. On the
    slow path (missing index, or a repo-root mismatch), `indexing_status` is
    set to `"in_progress"` and a background task is scheduled
    (`asyncio.create_task`, wrapping the actual build in `asyncio.to_thread`
    since `build_all_indexes_incremental`/`load_repo` are synchronous/blocking
    calls) — **`lifespan` still `yield`s server state immediately after
    scheduling that task, regardless of how long the build will take.** This
    is the concrete fix for the previous draft's "accepted blocking tradeoff":
    the MCP connection handshake completes at the same speed whether the repo
    is 10 files or 10,000, because nothing about establishing the connection
    waits on the build.
  - The background task, on completion, acquires `state.lock` (the same lock
    already used to guard reads of `.chunks` elsewhere), reloads
    `vector_index`/`bm25_index`/`manifest`/`reference_index` through the exact
    same `vector.load_index`/`bm25.load_index`/`manifest.load_manifest`/
    `references.load_index` calls the fast path uses (no special-cased
    in-memory handoff from the build step — the ready state is byte-identical
    whether the index existed already or was just built), sets
    `indexing_status="ready"`, and releases the lock. On failure, it instead
    sets `indexing_error` to the chained exception's message and
    `indexing_status="failed"`, under the same lock.
  - New shared guard, called as the first line of all six tool functions:
    ```python
    async def _require_index_ready(state: _ServerState) -> None:
        async with state.lock:
            status = state.indexing_status
            error = state.indexing_error
        if status == "in_progress":
            raise IndexBuildInProgressError(
                "Auto-indexing is still building this repo's index; retry shortly."
            )
        if status == "failed":
            raise AutoIndexError(error or "Auto-indexing failed for an unknown reason.")
    ```
  - `_build_server()` and `run()` gain new pass-through parameters
    (`repo_source: str | None`, `auto_index: bool`).
- `src/codebase_rag_mcp/cli/main.py` — `serve` subparser gains `--repo`,
  `--index-dir`, and `--no-auto-index` flags (mirroring `index`'s existing
  `source`/`--index-dir`/`--force` flags); `main()` passes them through to
  `mcp.server.run(...)`.
- `src/codebase_rag_mcp/config.py` — add `REPO_SOURCE` (optional override for the
  auto-index default source) and `AUTO_INDEX` (bool, default `True`) env-backed
  settings, plus a `_getenv_bool` helper following the existing `_getenv_int` /
  `_getenv_float` pattern; add both new names to `__all__`.
- `src/codebase_rag_mcp/mcp/exceptions.py` — add `AutoIndexError(MCPServerError)`
  for a clear, actionable failure message when auto-indexing itself fails (bad
  source, clone failure, zero files parsed, etc.) — now surfaced at the *next
  tool call* after a background failure, not only as a startup-time raise — and
  `IndexBuildInProgressError(MCPServerError)` for the still-building case.
  `IndexNotAvailableError` keeps its current meaning and its current
  synchronous, startup-time raise: it now fires only when `auto_index=False`
  (or `AUTO_INDEX=false`) and no usable index exists — that specific case is an
  intentional opt-out of zero-config behavior, so failing fast at connect time
  is still correct and unchanged from today.
- `.env.example` — document `REPO_SOURCE` and `AUTO_INDEX` under a new
  "Auto-indexing settings" section, following the file's existing
  commented-default convention, plus a short note that large repos may take a
  while to finish building in the background and tool calls will report
  "still building" until then.
- `DECISIONS.md` — log this design decision (background-task-not-blocking-
  lifespan, the `.git`-presence gate on the zero-config default path only, the
  same-repo-vs-different-repo manifest check, and the explicit accepted
  limitation that an already-valid index is never auto-refreshed just because
  the server restarted — see Rules).
- `FLOW.md` — document the new `serve` startup flow (load-existing → compare
  manifest repo_root → schedule background build if needed → yield ready
  immediately → tools guard on `indexing_status` → background task swaps in
  fresh state when done).

## Files to create
- `tests/test_mcp_auto_index.py` — new test module for the auto-index lifespan
  behavior, kept separate from `tests/test_mcp_server.py`'s existing tool-call
  tests, mirroring this project's existing one-module-per-concern layout (e.g.
  `test_indexing_incremental.py` vs `test_indexing_repo.py`).

## New dependencies
No new dependencies. Auto-indexing reuses `ingestion.loader.load_repo` and
`indexing.repo.build_all_indexes_incremental`, both already present and already
depended upon by the CLI `index` command; backgrounding uses stdlib
`asyncio.create_task`/`asyncio.to_thread`.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting for code
  (unaffected by this day, but never relaxed by it either).
- Every retrieved chunk and every citation carries exact file path + start/end
  line metadata.
- LLM answers must rely only on retrieved evidence; the existing "not enough
  evidence" fallback is untouched by this day.
- All LLM calls go through the provider fallback chain defined in CLAUDE.md —
  unaffected by this day.
- Structured outputs via Pydantic for any LLM call that isn't freeform prose —
  unaffected by this day.
- Embedding and reranker models run locally by default — no paid API in the
  default path; unaffected by this day.
- **Auto-index is a fallback, never a forced rebuild.** On every `serve`
  startup, first attempt the existing `vector.load_index` / `bm25.load_index`
  load exactly as today. Only trigger a build when that load fails to produce a
  usable index (`vector_index is None and bm25_index is None`), OR when a
  manifest already exists at `index_dir` but its resolved `repo_root` does not
  match the resolved effective source now being served. **Never rebuild just
  because the server restarted against an already-valid index for the same
  repo — this is a deliberate, explicitly accepted limitation, not an
  oversight: a file edited outside any MCP session will not be picked up until
  the next `codebase-rag index` run or a repo-root change forces a rebuild.
  State this plainly in DECISIONS.md so it is never assumed to be solved by
  this feature.**
- **Auto-indexing never blocks the MCP connection, regardless of repo size or
  whether the source is a local path or a remote URL requiring a clone.** The
  entire auto-index path — `load_repo`'s optional clone step *and* the
  `build_all_indexes_incremental` call — runs inside one background task
  (`asyncio.to_thread`), scheduled from `lifespan` before it `yield`s server
  state. `lifespan` never awaits that task. This is the direct fix for the
  previous draft's "accepted blocking tradeoff": MCP clients enforce startup
  timeouts (a 30s timeout on an unrelated plugin has already been directly
  observed in this same environment this session), so a synchronous full
  index build at connect time is not an acceptable trade-off, and this design
  removes it entirely rather than accepting the risk.
- **A tool call made while `indexing_status == "in_progress"` raises
  `IndexBuildInProgressError`; one made while `indexing_status == "failed"`
  raises `AutoIndexError` with the chained cause's message** — via the shared
  `_require_index_ready` guard called first by all six tools. Never a silent
  empty/degraded result indistinguishable from a genuine zero-evidence answer,
  and never an unhandled hang.
- **The zero-config default source is the local working directory, gated by a
  `.git`-presence check — never an implicit remote clone, and never an
  unguarded scan of an arbitrary directory.** Resolve the effective source with
  this precedence: `--repo` CLI flag → `REPO_SOURCE` env var → `os.getcwd()`
  **if and only if `os.getcwd()/.git` exists**. If none of `--repo`/
  `REPO_SOURCE` is set and no `.git` directory is present at `cwd`,
  auto-indexing is skipped entirely (fall back to requiring an explicit
  `codebase-rag index` run, i.e. today's `IndexNotAvailableError` path) —
  this is what stops the server from silently scanning and embedding an
  enormous or irrelevant directory (a home folder, a Downloads folder)
  just because it happened to be launched there. This `.git` gate applies
  **only** to the bare `os.getcwd()` fallback tier — an explicit `--repo`/
  `REPO_SOURCE` value (local path or `https://` URL) is a deliberate user
  instruction and is honored as-is, `.git` or not, exactly like the existing
  "never guess a remote URL unless explicitly configured" rule it sits
  alongside.
- **`--no-auto-index` / `AUTO_INDEX=false` preserves today's exact behavior**:
  raise `IndexNotAvailableError` immediately, synchronously, in `lifespan`, if
  no usable index exists — this is the one case where a synchronous
  connect-time failure is still correct, since it's an explicit opt-out of
  zero-config behavior entirely, not the zero-config path itself. This is the
  escape hatch for CI/test environments and for users who prefer the explicit
  `index`-then-`serve` workflow.
- **Auto-indexing runs synchronously inside `lifespan`** — REMOVED. See above:
  it now runs in a background task and never blocks `lifespan`'s `yield`.
- **A failure during the background auto-index build sets `indexing_status =
  "failed"` and `indexing_error` to the underlying cause's message** (chained
  via `from exc` internally when constructing that message), surfaced to the
  client at the next tool call via `AutoIndexError` — never a silent
  empty/partial index, and never a crash of the server process itself (the
  background task's exception is caught and stored, not left to propagate and
  kill the task silently).
- **Reuse `build_all_indexes_incremental` exactly as `cli/main.py`'s
  `_run_index` does** — do not duplicate its logic or introduce a second
  indexing code path inside `mcp/server.py`.
- Log clear `logger.info` start/elapsed/done lines to stderr only — never
  stdout, per this file's existing stdio-discipline docstring — so the
  operator can see background-build progress in the client's MCP server log
  even though no tool-call response blocks on it.
- Log every meaningful decision to `DECISIONS.md`, including: the background-
  task design and why (observed client startup timeout risk), the `.git` gate
  and why it's scoped to the implicit-default tier only, and the explicit
  no-auto-refresh-on-unchanged-reconnect limitation.
- Update `FLOW.md` with the new/changed pipeline path.

## Definition of done
- `codebase-rag serve --repo <local-path-with-no-existing-index> --index-dir
  <fresh-dir>` reaches ready state (the MCP connection itself succeeds)
  **immediately**, before the background build completes, against an actual
  demo repo (e.g. the `p-queue` clone already used in Day 11's benchmark run)
  — verified by a test that makes the build artificially slow (a monkeypatched
  `build_all_indexes_incremental` with a delay) and asserts `lifespan` yields
  well before that delay elapses. This is the direct test the previous draft's
  design was missing entirely.
- A tool call (`search_code`) made while the background build is still running
  raises `IndexBuildInProgressError` immediately, not after waiting for the
  build to finish; once the background build completes, the same tool call
  against the same session returns real, non-empty results with correct
  file/line citations.
- Running `codebase-rag serve` a second time against the same `--repo`/
  `--index-dir` (index now present, manifest `repo_root` matches) skips
  auto-indexing entirely — verified by a test asserting
  `build_all_indexes_incremental` is not called, `indexing_status` is
  `"ready"` immediately with no background task scheduled, and no
  "auto-indexing" log line appears on startup.
- Pointing `--repo` at a *different* local path while reusing an `--index-dir`
  that holds a manifest for the *first* repo triggers a fresh (background)
  auto-index for the new repo rather than silently serving stale/wrong-repo
  results — verified by asserting the reloaded manifest's `repo_root` matches
  the new source once `indexing_status` reaches `"ready"`.
- With no `--repo`/`REPO_SOURCE` set and no `.git` directory at `cwd`,
  auto-indexing is skipped and `IndexNotAvailableError` is raised exactly as
  today if no index exists — verified in a fixture directory with no `.git`.
- With no `--repo`/`REPO_SOURCE` set and a `.git` directory present at `cwd`,
  auto-indexing proceeds against `cwd` — verified in a fixture directory that
  has one.
- `--no-auto-index` (or `AUTO_INDEX=false`) with no existing index raises
  `IndexNotAvailableError` synchronously at connect time, exactly as today,
  with the same actionable message — unaffected by the `.git` gate.
- A background auto-index failure (e.g. `--repo` pointed at a nonexistent
  local path) sets `indexing_status="failed"`; the next tool call raises
  `AutoIndexError` with the underlying `ingestion`/`indexing` exception's
  message included, and the server process itself stays alive rather than
  crashing.
- `.env.example`, `DECISIONS.md`, and `FLOW.md` are all updated, including the
  explicit no-auto-refresh-on-unchanged-reconnect limitation; `pytest` passes
  including the new `tests/test_mcp_auto_index.py`; `mypy` and `ruff` are
  clean.