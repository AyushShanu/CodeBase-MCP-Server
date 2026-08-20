# codebase-rag-mcp

A Model Context Protocol (MCP) server that turns any local codebase into a
queryable, citation-grounded knowledge base. Built on a hybrid retriever
(dense FAISS + sparse BM25), an optional reranker, and a swappable LLM
provider (NVIDIA, Groq, OpenRouter, Gemini, or any local OpenAI-compatible
endpoint).

> **Status:** scaffold only. The package installs, the CLI runs, and the
> MCP server boots and advertises a placeholder `ping` tool. The RAG
> pipeline (ingestion → parsing → chunking → indexing → retrieval →
> reranking → generation) is being built incrementally on top of this
> skeleton. See `DECISIONS.md` and `FLOW.md`.

## Install

Requires **Python 3.11+**.

```bash
# Editable install with dev tooling (pytest, ruff, mypy)
pip install -e ".[dev]"
```

This pulls in tree-sitter, FAISS (CPU), rank-bm25, sentence-transformers,
langchain-huggingface / langchain-community, the official MCP Python SDK,
and httpx for outbound provider calls.

## Configure

Copy the example env file and fill in whichever provider keys you have:

```bash
cp .env.example .env
# then edit .env
```

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
| `DATA_DIR`             | Where ingested corpora live (default `./data`)                 |
| `INDEX_DIR`            | Where FAISS / BM25 artifacts persist (default `./data/index`)  |

## Run

```bash
# Print the version
codebase-rag --version

# Boot the MCP server over stdio (advertises a 'ping' tool today)
codebase-rag serve
```

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
  config.py                  # python-dotenv loader
  cli/main.py                # `codebase-rag` entrypoint
  mcp/server.py              # stdio MCP server (stub)
  ingestion/                 # file discovery (TBD)
  parser/                    # tree-sitter AST extraction (TBD)
  chunker/                   # AST-aware chunking (TBD)
  indexing/
    vector.py                # FAISS dense index (TBD)
    bm25.py                  # rank-bm25 sparse index (TBD)
  retrieval/                 # hybrid query routing (TBD)
  reranker/                  # cross-encoder / LLM reranker (TBD)
  generation/
    providers/               # NVIDIA / Groq / OpenRouter / Gemini / local (TBD)
  citations/                 # chunk → source citations (TBD)
  impact/                    # symbol-graph impact analysis (TBD)
```

## License

MIT. See [`LICENSE`](./LICENSE).
