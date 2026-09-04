# codebase-rag-mcp

A Model Context Protocol (MCP) server that turns any local codebase into a
queryable, citation-grounded knowledge base. Built on a hybrid retriever
(dense FAISS + sparse BM25), an optional reranker, and a swappable LLM
provider (NVIDIA, Groq, OpenRouter, Gemini, or any local OpenAI-compatible
endpoint).

> **Status:** the full RAG pipeline is implemented and MCP-connected:
> GitHub/local ingestion, Tree-sitter AST-aware chunking, hybrid
> FAISS + BM25 retrieval with a cross-encoder reranker, multi-provider
> LLM generation with citations, and a six-tool stdio MCP server
> (`search_code`, `find_symbol`, `get_file_context`, `ask`,
> `analyze_impact`, `repository_summary`) with zero-config auto-indexing
> on first connect. See `DECISIONS.md` and `FLOW.md` for the full
> build history and architecture.

## How It Works

*(This section was written by querying the project's own `codebase-rag`
MCP tools against its own indexed source — `repository_summary`,
`search_code`, `find_symbol`, and `analyze_impact` — rather than from
memory. Every claim below links back to a real file and, where useful,
line range. At the time of writing, this repo's own index reported 89
indexed Python files, 685 chunks, and 668 distinct symbols across three
top-level modules: `benchmarks`, `src`, `tests`.)*

### What this project does, in plain terms

It's a "chat with your codebase" server, but built to refuse to make
things up. Point it at a repo — a GitHub URL or a local path — and it
parses every file's real syntax tree, breaks the code into
function/class/method-sized chunks with exact file/line metadata, and
indexes those chunks two different ways (keyword search and semantic
vector search). When you ask a question, it retrieves the most relevant
chunks, reranks them for actual relevance, and only then asks an LLM to
write an answer — with a hard rule that the LLM cannot introduce a
citation for a piece of code it wasn't actually shown. The whole thing
is exposed as an MCP server, so any MCP-compatible AI coding tool
(Claude Code, Claude Desktop, Cursor, etc.) can call it as a set of tools
during a normal coding session.

### Architecture — how a query flows from indexing through retrieval to a final answer

**Indexing (offline, `codebase-rag index`):**

1. **Parsing** (`src/codebase_rag_mcp/parser/`) — `parser.extractor.parse_file`
   runs each file through Tree-sitter and walks the resulting AST twice:
   once to pull out functions/classes/methods/interfaces as `ParsedSymbol`s
   (with 1-indexed line ranges — Tree-sitter's own positions are 0-indexed
   and get converted before a `ParsedSymbol` is ever built), and once more
   over the *same already-parsed tree* to extract call/import references
   used later by `analyze_impact`. A method nested in a class gets a
   *qualified* name (`"ClassName.method"`) so two classes with a
   same-named method never collide. A malformed file never aborts the
   run — syntax errors are recorded in `parse_errors` and parsing
   continues opportunistically.
2. **Chunking** (`src/codebase_rag_mcp/chunker/`) — `chunker.chunker.chunk_file`
   turns each `ParsedSymbol` into one retrievable `Chunk`, carrying
   `repo, file, symbol, type, language, start_line, end_line, content`
   plus a deterministic `id` (derived from file + symbol + start line, so
   re-indexing an unchanged file produces byte-identical chunk IDs and
   citations stay stable across re-runs). A symbol that's too large is
   split by `chunker.fallback.split_oversized_symbol` along in-span line
   boundaries into `name#part1`, `name#part2`, ... — verified directly:
   splitting a 25-line symbol at `max_chunk_lines=10` produces three
   contiguous, non-overlapping spans. A file with zero extractable
   symbols still gets one whole-file fallback chunk, so nothing is
   silently dropped.
3. **Dual indexing** (`src/codebase_rag_mcp/indexing/`) — the same chunk
   collection is indexed twice: `indexing.vector` embeds every chunk
   locally with `all-MiniLM-L6-v2` (via `langchain_huggingface
   .HuggingFaceEmbeddings`, `normalize_embeddings=True`) into a hand-rolled
   persistent FAISS `IndexFlatIP` index over L2-normalized vectors
   (`vector.faiss` + a parallel `vector_metadata.json` keyed by FAISS
   vector ID); `indexing.bm25` builds a `rank_bm25.BM25Okapi` sparse index
   over the same chunks. A `manifest.json` records the checkout root so
   `get_file_context` can later resolve citations back to real files on
   disk.

**Query time (the `search_code` / `ask` MCP tools):**

4. **Hybrid retrieval** (`src/codebase_rag_mcp/retrieval/hybrid.py`) —
   `hybrid_search` queries the BM25 and FAISS indexes independently, then
   merges the two ranked candidate lists via **Reciprocal Rank Fusion**:
   each side's rank-`r` result contributes `1 / (RRF_K + r)` to a chunk's
   merged score, summed across both sides. Every `HybridQueryResult` keeps
   its BM25 rank/score *and* vector rank/score alongside the merged score
   — never collapsed into an opaque single number — so it's possible to
   see exactly why a chunk ranked where it did. If a chunk is found by
   only one side, the other side's rank/score is left `None`, never a
   fabricated `0` (which would be indistinguishable from a genuine top
   rank). If *both* indexes are unavailable, it raises
   `NoIndexAvailableError`; if only one is missing, it degrades to
   single-source search with a warning rather than failing outright.
5. **Reranking** (`src/codebase_rag_mcp/reranker/rerank.py`) — the wide
   hybrid candidate pool is re-scored by a `CrossEncoder`
   (`cross-encoder/ms-marco-MiniLM-L-6-v2`) in a single batched
   `.predict()` call over every `(query, chunk.content)` pair, then sorted
   by that cross-encoder score and truncated to the top N. Each
   `RerankedResult` still carries the full underlying `HybridQueryResult`,
   so the entire scoring chain — BM25 rank/score → vector rank/score →
   RRF score → cross-encoder score — is inspectable end to end, not just
   the final ranking.
6. **Generation** (`src/codebase_rag_mcp/generation/`) — `generation
   .pipeline.generate_answer` builds a file/line-formatted evidence prompt
   from the reranked chunks and calls a configured LLM provider (see
   below) for a structured JSON response: an answer, a list of
   `cited_chunk_ids`, and a `has_sufficient_evidence` flag.
7. **Citations** (`src/codebase_rag_mcp/citations/attach.py`) —
   `attach_citations` turns the model's `cited_chunk_ids` into real
   `Citation` objects, and this is where the anti-fabrication guarantee
   actually lives (see below).

### What makes the chunking/retrieval approach different from naive RAG

Naive RAG over code typically splits files by a fixed character/token
window, which routinely slices a function in half and destroys the
structure an LLM would need to reason about it correctly. This project
never does that. Chunking is driven entirely by the real AST
(`parser.extractor.parse_file` → `chunker.chunker.chunk_file`): a chunk
boundary is always a real symbol boundary (a function, class, method, or
interface), confirmed directly in `chunker/models.py`'s `Chunk` model and
`chunker/fallback.py`'s oversized-symbol splitter, which — even when a
single symbol is too large for one chunk — only ever splits *within* that
symbol's own span, never across unrelated code. Every chunk and every
downstream citation therefore carries an exact `file`, `start_line`, and
`end_line`, not an approximate "somewhere in this file" pointer.

Retrieval is also intentionally hybrid rather than vector-only. A pure
embedding search is weak on exact identifiers (a function name like
`generateToken` is a token match, not really a semantic one), while a
pure keyword search misses conceptual queries ("where is authentication
handled?") that don't share vocabulary with the code. `retrieval/hybrid.py`
runs both and merges them via Reciprocal Rank Fusion instead of a single
opaque similarity score, and a real test in the repo
(`test_hybrid_search_chunk_found_by_both_outranks_chunk_found_by_one_side`)
verifies a chunk found by *both* BM25 and vector search outranks one
found by only one side — the concrete behavior RRF is there to produce.
The cross-encoder reranking stage on top of that is a second, more
expensive pass that reads the actual `(query, candidate)` pair jointly
(rather than comparing independently-embedded vectors), which is why it
runs only over the already-narrowed hybrid pool rather than the whole
index.

### The anti-fabrication mechanism

This is enforced mechanically, not just by prompt instructions. The key
design decision, visible directly in `citations/models.py`'s `Citation`
docstring: a `Citation`'s `file`, `symbol`, `start_line`, and `end_line`
are **always** copied from this project's own indexed `Chunk` metadata in
`citations/attach.py`'s `attach_citations` — **never** from anything the
LLM itself asserts. The model is only ever allowed to supply *which*
`chunk_id` it used; it cannot originate a file path or line number that
lands in a citation, because that path doesn't exist in the code.

Concretely, `attach_citations` builds a `chunk_id → candidate` lookup
from the real retrieved evidence and walks the model's `cited_chunk_ids`
against it. An ID that doesn't match anything in the actual candidate set
is **silently dropped and logged as a warning, never raised** — the code
comment is explicit that "a model over-citing or citing a stale ID is
expected, handled input, not a bug." Then, in `generation/pipeline.py`'s
`generate_answer`, the final `has_sufficient_evidence` flag is forced to
`structured.has_sufficient_evidence AND bool(citations)` — meaning even
if the model *claims* sufficient evidence while citing something that
resolved to zero real citations, the answer is downgraded to
insufficient-evidence regardless of what the model said.

I confirmed this isn't just a described intention — it's tested directly
against an adversarial case:
`test_generate_answer_drops_fabricated_citation_even_when_fake_provider_obeys_adversarial_evidence`
simulates a "compromised" model that was prompt-injected into returning
`answer="PWNED"` with a `cited_chunk_ids` list naming a chunk that never
existed. The test asserts the mechanical backstop still holds:
`result.citations == []` and `result.has_sufficient_evidence is False`,
regardless of what the model was tricked into asserting. A query with
zero retrieved candidates never even calls an LLM provider at all — it
returns the canned insufficient-evidence answer for free.

The same pattern is reused one level up for the prose narratives behind
`analyze_impact` and `repository_summary`
(`impact/explain.py:explain_impact`, `impact/summary.py
:explain_repository_summary`): the LLM's structured JSON output is
checked for any `referenced_files`/`referenced_modules` not present in
the real evidence set, and a fabrication is treated as a retry-worthy
failure — identical in kind to a JSON-schema validation error — which
rebuilds the prompt with a fabrication-specific correction and retries,
up to a configured retry budget, before moving to the next provider in
the fallback chain.

Providers themselves are chained with runtime fallback, not hardcoded to
one vendor: `generation/providers/registry.py`'s `select_providers`
returns only the providers whose credentials are actually configured, in
a fixed **NVIDIA → Groq → OpenRouter → Gemini → Local** precedence order,
and `generate_answer` walks that list, giving each provider its own JSON
retry budget before falling through to the next — raising
`AllProvidersFailedError` only once every configured provider has
failed. This degrade-gracefully behavior is real, not theoretical, in
this exact session: calling `repository_summary` against this repo's own
index returned `explanation: null` — no provider is currently configured
here, and the tool returned the deterministic structural data with a
`None` narrative instead of erroring or inventing one, exactly as the
code above says it should.

### How it's packaged and portable across MCP clients

The server ships as an installable CLI (`codebase-rag`, via `pyproject.toml`,
installable with `pipx install .` or `uvx --from .`) exposing two
subcommands — `index` and `serve` — and `codebase-rag serve` speaks MCP
over plain stdio, which is what makes it launchable by any MCP-compatible
client as a subprocess: Claude Desktop, Claude Code, Cursor, or any other
stdio-based host (a client like OpenCode that speaks the same stdio MCP
protocol can launch it the same way — a matching JSON config snippet
just isn't included in this README yet, only Claude Desktop/Code and
Cursor are).

The portability problem this closes (documented directly in
`config._resolve_index_dir`'s docstring and enforced by
`InvalidIndexDirError`) is that an MCP client controls the subprocess's
working directory, not this project — so anything that resolved
`DATA_DIR`/`INDEX_DIR` relative to `cwd` would silently point at a
different place depending on which client launched it. Instead:

- With no explicit `--index-dir`/`INDEX_DIR`, the index directory is
  keyed by a 16-hex sha256 hash of the *canonicalized* repo source, under
  an OS-appropriate `platformdirs.user_data_dir("codebase-rag")` path —
  so the same repo always resolves to the same index directory no matter
  which directory or MCP client launched the server (verified by
  `test_resolve_index_dir_same_local_repo_same_result_regardless_of_cwd`),
  and two different repos never collide
  (`test_resolve_index_dir_two_local_repos_do_not_collide`).
- An explicit `--index-dir`/`INDEX_DIR` is honored, but **must be
  absolute** — a relative value is rejected outright with
  `InvalidIndexDirError` rather than silently resolved against `cwd`,
  since `cwd` is exactly the launch-directory dependency this mechanism
  exists to remove.
- `cli/main.py`'s `serve` dispatch resolves the effective repo source,
  index directory, and `.env` file *before* importing `mcp.server` at
  all, then reloads `config` — because many indexing/generation
  submodules capture config defaults at their own import time, so
  resolving this after import would be too late for it to take effect.
- Provider keys are read from the real process environment first (so an
  MCP client's own `"env"` config block always wins) and only fall back
  to a `.env` file — never the reverse.

Full install steps and copy-pasteable per-client JSON configs are below.

## Install

Requires **Python 3.11+**.

Not yet published to PyPI — install from a clone of this repo:

```bash
# For end users: an isolated, globally-available `codebase-rag` command
git clone <this-repo-url> && cd codebase-rag-mcp
pipx install .
# ...or run it without a separate install step:
uvx --from . codebase-rag serve --repo <path-or-url>
```

```bash
# For development: editable install with dev tooling (pytest, ruff, mypy)
pip install -e ".[dev]"
```

This pulls in tree-sitter, FAISS (CPU), rank-bm25, sentence-transformers,
langchain-huggingface / langchain-community, the official MCP Python SDK,
and httpx for outbound provider calls.

## Configure

**Recommended (any packaged/installed use, including every MCP client
below):** set provider keys directly in the client's own server
`"env"` config block — see "Connect to an MCP client" below. No `.env`
file is required for this.

**For local development**, copy the example env file and fill in
whichever provider keys you have:

```bash
cp .env.example .env
# then edit .env
```

A variable already set in the real process environment (a client's
`"env"` block, a shell export) is **never** overridden by any `.env`
file, regardless of which one is loaded — see `.env.example` for the
full discovery precedence.

Recognized variables (see `.env.example` for the full list):

| Variable               | Purpose                                                        |
| ---------------------- | -------------------------------------------------------------- |
| `NVIDIA_API_KEY`       | NVIDIA NIM / build API                                         |
| `GROQ_API_KEY`         | Groq Cloud                                                     |
| `OPENROUTER_API_KEY`   | OpenRouter (multi-provider proxy)                              |
| `GEMINI_API_KEY`       | Google Gemini (optional)                                       |
| `LOCAL_MODEL_BASE_URL` | OpenAI-compatible local server (Ollama, vLLM, LM Studio, ...)  |
| `LOCAL_MODEL_NAME`     | Model name to use against the local server                     |
| `LOCAL_MODEL_API_KEY`  | Optional bearer token for the local server                     |
| `LOG_LEVEL`            | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`)        |
| `DATA_DIR`             | Where cloned corpora are staged (default `./data`)              |
| `INDEX_DIR`            | Where FAISS/BM25 artifacts persist — default is **not** a fixed path; see below. Must be absolute if set. |
| `REPO_SOURCE`          | Default repo (URL or local path) to zero-config auto-index      |
| `AUTO_INDEX`           | Set `false` to require a prebuilt index (default `true`)        |

`INDEX_DIR` defaults to a per-repo directory keyed by a hash of the
resolved repo source, under an OS-appropriate user-data path — the same
repo always resolves to the same index directory regardless of which
directory or MCP client launched the server, and two different repos
never collide. Setting `INDEX_DIR` (or `--index-dir`) always overrides
this, but the value must be absolute — a relative path is rejected
outright.

## Run

```bash
# Print the version
codebase-rag --version

# Index a repo once (optional -- `serve` will also auto-index on first
# connect if no index exists yet)
codebase-rag index https://github.com/some-org/some-repo
# ...or a local path:
codebase-rag index /path/to/local/repo

# Boot the MCP server over stdio
codebase-rag serve --repo /path/to/local/repo
```

## Connect to an MCP client

`codebase-rag serve` speaks MCP over stdio, so any MCP-compatible client
can launch it as a subprocess. Every snippet below passes `--repo`
explicitly rather than relying on zero-config `cwd`-based detection:
a client's subprocess launch directory is that client's own choice, not
something this project controls, so an explicit `--repo` is the one
setting that is *always* correct regardless of it (see DECISIONS.md
D-027). If you've confirmed a specific client happens to launch with
`cwd` at your project root, `--repo` can be dropped as a convenience —
just never as a requirement.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "codebase-rag": {
      "command": "codebase-rag",
      "args": ["serve", "--repo", "/absolute/path/to/your/repo"],
      "env": {
        "GROQ_API_KEY": "your-key-here"
      }
    }
  }
}
```

**Claude Code** (project-level `.mcp.json`, or `claude mcp add`):

```json
{
  "mcpServers": {
    "codebase-rag": {
      "command": "codebase-rag",
      "args": ["serve", "--repo", "/absolute/path/to/your/repo"],
      "env": {
        "GROQ_API_KEY": "your-key-here"
      }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`, or project-level `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "codebase-rag": {
      "command": "codebase-rag",
      "args": ["serve", "--repo", "/absolute/path/to/your/repo"],
      "env": {
        "GROQ_API_KEY": "your-key-here"
      }
    }
  }
}
```

If `codebase-rag` isn't on the client's `PATH` (common for a GUI app
that doesn't inherit your shell profile), use its full path from
`which codebase-rag` as `"command"` instead.

## Develop

```bash
ruff check .            # lint
ruff format --check .   # format check
mypy                    # type-check
pytest                  # tests
```

A preconfigured GitHub Actions workflow at `.github/workflows/ci.yml`
runs all four on every push.

## Layout

```
src/codebase_rag_mcp/
  config.py                  # env/config resolution -- provider keys, INDEX_DIR/DATA_DIR/.env discovery
  cli/main.py                # `codebase-rag` entrypoint (index / serve subcommands)
  mcp/server.py               # stdio MCP server: 6 tools + zero-config auto-indexing
  ingestion/                 # GitHub/local repo loading, file filtering, language detection
  parser/                     # Tree-sitter AST extraction
  chunker/                    # AST-aware chunking (+ oversized-symbol fallback splitting)
  indexing/
    vector.py                # FAISS dense index
    bm25.py                  # rank-bm25 sparse index
    references.py            # symbol reference/import index (for analyze_impact)
    cache.py                 # incremental-indexing chunk cache
  retrieval/                 # hybrid BM25 + vector retrieval (Reciprocal Rank Fusion)
  reranker/                  # cross-encoder reranking of the hybrid candidate pool
  generation/
    providers/               # NVIDIA / Groq / OpenRouter / Gemini / local -- fallback chain
  citations/                 # chunk -> file/line citation formatting
  impact/                    # symbol lookup, reference analysis, analyze_impact, repository_summary
```

## License

MIT. See [`LICENSE`](./LICENSE).
