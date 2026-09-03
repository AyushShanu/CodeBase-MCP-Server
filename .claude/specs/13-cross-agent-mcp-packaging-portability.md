# Spec: Cross-Agent MCP Packaging & Portability

## Overview
Every prior MCP day (08 mcp-server-v1, 11 v2-polish-evaluation, 12
zero-config-auto-indexing) was built and tested from this repo's own dev
shell, where `cwd` is always the project root and `.env` always sits right
next to it. Real MCP clients don't guarantee that: Claude Desktop, Cursor,
Windsurf, Cline, and other stdio-based hosts each launch the configured
server command with their *own* working directory — often the client
application's install dir, the user's home directory, or unset entirely —
not the directory the user "thinks of" as their project. Concretely: the
same `codebase-rag serve` command, added once to Claude Desktop's config and
once to Claude Code's, can each resolve `INDEX_DIR` to a *different*
location, silently trigger a fresh zero-config auto-index build in the wrong
place, or fail to pick up the user's provider keys from `.env` at all.

This day fixes what's actually fixable in code — `INDEX_DIR`/`DATA_DIR`
resolution and provider-key discovery, made fully `cwd`-independent and
collision-proof across simultaneously-used repos — and is explicit, not
silent, about the one thing that *isn't* fixable in code: Day 12's
`.git`-gated zero-config source detection only works for a client that
happens to launch the server with `cwd` set to the project root. There is
no code fix for a client that doesn't do that — it's a property of how that
client launches subprocesses, not of this codebase. So this day's job for
*that* half of the problem is to actually test and honestly document, per
client, whether zero-config source detection works there, and to make the
explicit-`--repo` fallback a first-class, clearly-documented path for
clients where it doesn't — not to pretend a universal code fix exists where
none can.

It belongs right after Day 12 because it depends on that day's
`--repo`/`REPO_SOURCE`/cwd-fallback resolution order and would otherwise
just be adding portability veneer on top of a still cwd-coupled foundation.

## Depends on
- Day 08 (`feature/mcp-server-v1`) — the stdio MCP server and its six
  tools must exist.
- Day 11 (`feature/v2-polish-evaluation`) — CLI polish, incremental
  indexing, and the security controls (path restriction, secret-file
  exclusion) this day must not regress.
- Day 12 (`feature/zero-config-auto-indexing`, filed at `day_number=12`
  per D-026) — `serve`'s `--repo`/`REPO_SOURCE`/cwd-fallback resolution
  order and the background auto-index task. **This day consumes that
  resolution's *output* (the effective, already-resolved repo source) as
  the input to its own `INDEX_DIR`-keying scheme (see Files to change) —
  it does not change Day 12's resolution order or its `.git` gate.** Making
  that resolution itself launch-directory-independent is not possible in
  code (see Overview) and is explicitly out of scope; this day's job is to
  test and document, per client, whether it holds.

## Pipeline stage(s) touched
MCP Server only. No ingestion, parsing, chunking, embedding, BM25,
retrieval, reranking, generation, or citation logic changes.

## MCP tools affected
No MCP tool changes. `search_code`, `find_symbol`, `get_file_context`,
`ask`, `analyze_impact`, and `repository_summary` keep their existing
schemas and behavior — this day changes *how the server process is
configured and launched*, not what it does once running.

## Model / provider choice for this step
Not applicable — this day touches no LLM, embedding, reranker, or
vector-store selection. No new model/provider decision to make or flag.

## Files to change
- `src/codebase_rag_mcp/config.py`:
  - **Decided, not deferred: `DATA_DIR`/`INDEX_DIR` default to a fixed
    OS-appropriate user-data directory (via `platformdirs.user_data_dir
    ("codebase-rag")`), keyed per-repo — never a single shared location.**
    The key is derived from the *already-resolved* effective repo source
    (Day 12's `--repo` → `REPO_SOURCE` → `.git`-gated-cwd-fallback output,
    passed in after that resolution runs, not recomputed here): canonicalize
    a local path (resolve symlinks, make absolute) or normalize a remote
    URL, then take a short (e.g. 16-hex-char) `hashlib.sha256` digest of
    that canonical string as the subdirectory name — e.g.
    `<user_data_dir>/index/<repo-hash>/`. This is the concrete fix for the
    collision risk a naive "one fixed directory" design would have: two
    different repos resolve to two different hashes, automatically, with
    zero explicit per-project config, and can never collide. An explicit
    `--index-dir`/`INDEX_DIR` always overrides this entirely and is
    resolved to an absolute path immediately if given relatively (fail
    loudly, not silently, if it can't be made absolute).
  - `.env` discovery order, in this precedence (first found wins for any
    given variable; **a variable already set in the process environment —
    e.g. by an MCP client's own `"env"` config block for this server — is
    never overridden by any `.env` file**, since `load_dotenv(override=
    False)` already guarantees this): (1) an explicit `--env-file` path if
    given; (2) a `.env` file at the resolved effective repo source's root,
    if the source is local; (3) `os.getcwd()/.env` (today's exact original
    behavior, kept as the final fallback so existing dev workflows launched
    from the repo root are unaffected). **The primary, recommended path for
    a packaged/installed server is not a discovered `.env` file at all — it
    is provider keys set directly in the MCP client's own server
    registration `"env"` block**, which every client this day documents
    supports; `.env` discovery exists for the "still developing inside a
    cloned checkout" case, not as the production mechanism.
  - Add a `_resolve_index_dir(repo_source: str, explicit: str | Path | None)
    -> Path` and `_resolve_env_path(repo_source: str, explicit: str | Path
    | None) -> Path | None` pair of pure, independently-testable functions
    implementing the above, instead of inlining this logic into module-level
    side effects — this is what makes `tests/test_config_portability.py`
    possible without spawning real subprocesses for every case.
- `src/codebase_rag_mcp/cli/main.py` — `serve` gains `--env-file`
  (documented above); `--index-dir`'s help text updated to state the new
  keyed-user-data-dir default and that a relative value is rejected outright
  rather than silently resolved against `cwd`.
- `src/codebase_rag_mcp/mcp/server.py` — `_lifespan` logs the resolved
  `INDEX_DIR`, the repo-hash key, and which `.env` path (if any) was
  actually used, at `INFO` level to stderr, before doing anything else —
  so a misconfigured client is debuggable from its own captured server log
  without needing to reproduce the issue by hand.
- `pyproject.toml` — add `platformdirs` to `dependencies` (confirmed
  direction, not deferred — see New dependencies); confirm `[project.
  scripts]`'s `codebase-rag` entry point is what the `pipx`/`uvx` install
  test actually exercises, not `python -m`.
- `.env.example` — document the new resolution order, and add a prominent
  note that the recommended way to supply provider keys to a packaged
  install is the MCP client's own `"env"` config block, with `.env`
  presented as the dev-checkout convenience path.
- `README.md`:
  - Replace the stale "scaffold only" status line.
  - Add a "Connect to an MCP client" section with copy-pasteable config
    snippets for Claude Desktop, Claude Code, and **Cursor** (the "one more
    stdio-based client" this day commits to, chosen because it's widely
    used and its config format is well-documented) — each snippet sets
    provider keys via that client's `"env"` block, not a `.env` file.
  - **For each client, state plainly, based on the actual test performed
    for this day (see Rules/DoD), whether that client launches the server
    with `cwd` set to the project root** — and therefore whether Day 12's
    zero-config `.git`-gated source detection actually works there. For
    any client where it doesn't (this is the expected outcome for at least
    one of the three, per the Overview), that client's snippet includes an
    explicit `--repo <absolute-path>` (or the client's env-var equivalent)
    instead of relying on cwd inference — documented as the permanent,
    correct way to configure that client, not a workaround.
  - Add `pipx`/`uvx` install instructions alongside the existing editable-
    install instructions.
- `CLAUDE.md` — already updated in this same change to add the Day 13
  roadmap entry (done as part of spec creation, not implementation).

## Files to create
- `.claude/specs/13-cross-agent-mcp-packaging-portability.md` — this
  spec file.
- `tests/test_config_portability.py` — covers, against the new
  `_resolve_index_dir`/`_resolve_env_path` pure functions directly (no
  subprocess needed):
  - two different resolved repo sources (local paths, and separately two
    different remote URLs) produce two different, non-colliding
    `INDEX_DIR` values under the shared user-data root — the direct,
    permanent regression test for the exact "two repos silently share one
    index" bug class this project has already hit more than once;
  - the same repo source, resolved twice (e.g. once from `cwd=/tmp/a`,
    once from `cwd=/tmp/b`, with the same `--repo` given explicitly both
    times), produces the *same* `INDEX_DIR` both times — proving the
    resolution is genuinely `cwd`-independent, not just collision-avoidant;
  - an explicit `--index-dir` always wins over the keyed default, and a
    relative one is rejected rather than silently resolved against `cwd`;
  - `.env` discovery precedence (explicit `--env-file` → repo-root `.env`
    → `cwd`-relative `.env` → none found) in isolation from any real
    environment variables;
  - a variable already present in `os.environ` before `load_dotenv` runs is
    never overridden by any discovered `.env` file's value, simulating the
    "MCP client set it via its own env block" case with no `.env` file
    present anywhere.

## New dependencies
`platformdirs` — small, no transitive dependencies, actively maintained,
genuinely cross-platform user-data-directory resolution. **Direction
confirmed as part of this spec, not deferred to implementation** (see Files
to change): the keyed-fixed-user-data-dir strategy requires it, and the
alternative (require-an-absolute-`--index-dir`-always, no default at all)
was rejected because it would reintroduce exactly the per-client manual
per-project config this whole effort exists to remove. No new free API key
required — this day is packaging/config only, not a new provider
integration.

## Rules for implementation
- AST-aware chunking only — never fixed-size/character-count splitting
  for code (not touched by this day, but stays true of the codebase).
- Every retrieved chunk and every citation carries exact file path +
  start/end line metadata (not touched by this day).
- LLM answers must rely only on retrieved evidence; the existing
  "not enough evidence" fallback must keep working unchanged (not
  touched by this day).
- All LLM calls go through the provider fallback chain defined in
  CLAUDE.md — never hardcode a single provider (not touched by this
  day).
- Structured outputs via Pydantic for any LLM call that isn't freeform
  prose (not touched by this day).
- Embedding and reranker models run locally by default — no paid API in
  the default path (not touched by this day).
- Do not change any MCP tool's request/response schema in this day —
  this is a launch/config-plumbing day, not a tool-contract day.
- Do not weaken the Day 11 security controls (path restriction outside
  repo root, secret-file exclusion) while changing path resolution —
  any new path-resolution code must be reviewed against
  `PathOutsideRepoRootError` and friends in `mcp/exceptions.py`.
- Whatever resolution strategy is chosen must be deterministic and
  loggable: given the same effective repo source, the server must resolve
  to the same `INDEX_DIR` every time, and that resolved path (plus the
  `.env` path actually used, if any) must be visible in the server's own
  startup log/stderr for debugging a misconfigured client.
- **`INDEX_DIR` keying is per-resolved-repo-source, never per-`cwd`.** The
  repo-hash key is computed from the canonicalized effective source *after*
  Day 12's resolution runs, not from `os.getcwd()` directly — this is what
  makes the same repo resolve to the same index regardless of which
  directory the client happened to launch from.
- **Do not claim this day makes zero-config source detection
  launch-directory-independent — it cannot be, in code.** For each client
  documented in the README, the actual `cwd` a real launch uses must be
  tested and stated plainly, and any client where it isn't the project root
  gets an explicit `--repo` (or client-native env-var equivalent) in its
  documented config, not a false promise that zero-config "just works"
  there too.
- A provider key already present in the process environment (however it
  got there — an MCP client's `"env"` block, a shell export, etc.) is
  never overridden by a discovered `.env` file's value, in either
  direction of this precedence.
- Log every meaningful decision to `DECISIONS.md` — in particular the
  keyed-user-data-dir strategy and the hash/canonicalization scheme used,
  the `.env`-vs-client-env-block precedence, and the actual measured `cwd`
  behavior of each tested client.
- Update `FLOW.md` if the resolved config/path now flows through
  `_lifespan`/`run` differently than documented in its "Component map"
  / offline build flow sections.

## Definition of done
- [ ] `codebase-rag serve --repo <same-repo>` run with `cwd` set to two
      *different*, unrelated directories (neither one the repo root)
      resolves to the *same* `INDEX_DIR` both times — verified by a real
      invocation, not just the unit test mock.
- [ ] `codebase-rag serve --repo <repo-A>` and `codebase-rag serve --repo
      <repo-B>` (two different real repos, no explicit `--index-dir` for
      either) resolve to two distinct, non-colliding `INDEX_DIR` values —
      verified by a real invocation of both, confirming their persisted
      index files land in different directories. This is the direct,
      permanent test for the collision class this project has already hit
      more than once under manual configuration.
- [ ] `tests/test_config_portability.py` passes, covering all the cases
      listed under Files to create.
- [ ] A clean `pipx install .` (or `uvx --from . codebase-rag`, whichever
      the implementation settles on) from a fresh shell — not the dev
      `.venv` — successfully runs `codebase-rag --version` and
      `codebase-rag serve` against a real indexed repo.
- [ ] A provider key supplied *only* via a simulated MCP-client `"env"`
      block (the variable set directly in the subprocess environment, with
      no `.env` file present anywhere on disk) is correctly picked up and
      used by a real `ask` call — direct proof of the packaged/installed
      case named in the Overview as one of the two original problems, not
      left implicitly assumed to work.
- [ ] README's "Connect to an MCP client" section's Claude Desktop config
      snippet is copy-pasted into an actual Claude Desktop
      `claude_desktop_config.json`, and the server connects and answers
      one of the four non-negotiable demo questions from CLAUDE.md
      end-to-end (evidence this isn't just a docs exercise).
- [ ] The same live test is repeated against Cursor (or whichever second
      client was chosen), and the actual observed `cwd`-at-launch behavior
      for that client is recorded in `DECISIONS.md` and reflected honestly
      in its README config snippet — including an explicit `--repo` in
      that snippet if zero-config source detection turned out not to work
      there.
- [ ] The existing full test suite (`pytest`) still passes with no
      regressions to Day 08/11/12 MCP server behavior.
- [ ] `ruff check .`, `ruff format --check .`, and `mypy` all pass clean.
- [ ] `DECISIONS.md` has a new entry covering: the keyed-user-data-dir
      strategy and why the naive single-shared-directory alternative was
      rejected; the `platformdirs` dependency decision; the `.env`-vs-
      client-env-block precedence; and the measured `cwd`-at-launch
      behavior of each client actually tested.
- [ ] `FLOW.md` is updated if the resolved-config path changed how data
      flows into `_lifespan`/`run`.
- [ ] README's stale "scaffold only" status line is corrected to reflect
      actual repo state.