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
  mcp/server.py              # stdio MCP server: 6 tools + zero-config auto-indexing
  ingestion/                 # GitHub/local repo loading, file filtering, language detection
  parser/                    # Tree-sitter AST extraction
  chunker/                   # AST-aware chunking (+ oversized-symbol fallback splitting)
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
