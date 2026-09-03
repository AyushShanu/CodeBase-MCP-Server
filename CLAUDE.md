# Codebase RAG MCP Server — Project Memory

## Overview
An AI developer assistant that indexes a real GitHub repository, understands code structure via Tree-sitter/AST-aware chunking, retrieves relevant code using hybrid BM25 + vector search, reranks the results, generates citation-backed answers, and exposes it all through an MCP server so Claude Code/Claude Desktop can use it. Ships open-source, MIT-licensed, clone-and-run — anyone can use it with only free API keys, no hosted backend required. This file is the source of truth for the roadmap, architecture, stack, and conventions — read it before starting any day's work. Full design rationale lives in `Codebase_RAG_MCP_Final_Plan.md`.

## Constraints
- **Plan window:** 20–31 August 2026 (12 days). Working V1 > polished V2 > extra features — cut scope, not the core retrieval + MCP demo, if behind.
- **Distribution:** open-source, MIT license, bring-your-own-free-API-key. No hosted multi-user service, no remote-MCP-OAuth, no billing in this sprint.
- **Hardware:** MacBook, Apple M5, 16GB unified memory, 512GB SSD. Local embedding/reranker/index footprint is well under 1GB — no concern. Optional local LLM mode: Qwen2.5-Coder-7B-Instruct at Q4_K_M (~4.5–5GB) fits comfortably. Never ship a large model crushed to Q2_XXS as the default — 2-bit quality loss undermines citation accuracy, which is the whole point of this tool.

## Architecture
```
GitHub Repository
  → Repository Loader → File Filtering
  → Tree-sitter Parsing → AST-aware Code Chunks + file/line metadata
  → Embeddings (local)  ─┐
  → BM25 Index (local)  ─┤→ Hybrid Retrieval → Reranker (local)
                          ┘
  → LLM Answer Generation → File/Line Citations
  → MCP Server → Claude Code / Claude Desktop
```

## Tech Stack — options are intentional, pick deliberately per spec
| Layer | Primary | Alternative | Alternative |
|---|---|---|---|
| LLM generation | NVIDIA NIM | Groq | OpenRouter *(+ optional Gemini, + optional local Qwen2.5-Coder-7B Q4_K_M for offline mode)* |
| Embeddings (local only) | `all-MiniLM-L6-v2` (start here, Day 4) | `jina-embeddings-v2-base-code` (swap before final) | `bge-small-en-v1.5` |
| Reranker (local only) | `cross-encoder/ms-marco-MiniLM-L-6-v2` | `bge-reranker-base` | `mxbai-rerank-base-v1` |
| Vector store | FAISS (`faiss-cpu`) | Qdrant (local Docker or free cloud tier) | Chroma |
| Keyword search | `rank-bm25` | — | — |
| Parsing | Tree-sitter (`py-tree-sitter`) | — | — |
| MCP | Official MCP Python SDK | — | — |

LLM generation always goes through the fallback chain (NVIDIA NIM → Groq → OpenRouter), never a single hardcoded provider — reuse the provider-agnostic LLM layer pattern from ResearchOS.

## Conventions
- AST-aware chunking only — never fixed-size/character-count splitting for code
- Every chunk and every citation carries exact file path + start/end line metadata
- LLM answers rely only on retrieved evidence; an explicit "not enough evidence" fallback is required — no fabricated citations
- Structured outputs via Pydantic for any LLM call that isn't freeform prose
- Embedding and reranker models are local by default — no paid API in the default path
- Feature branches: `feature/<slug>`
- Specs live in `.claude/specs/<day_number>-<feature_slug>.md`
- Every meaningful decision gets logged to `DECISIONS.md` with reasoning
- Every new/changed pipeline path gets documented in `FLOW.md`

## MCP Tool Contract
| Tool | Purpose | Version |
|---|---|---|
| `search_code` | Hybrid retrieval search, returns ranked code evidence | V1 |
| `find_symbol` | Find a function/class/method/interface and its definition/usages | V1 |
| `get_file_context` | Return an exact file/line range for additional context | V1 |
| `ask` | Full RAG pipeline: retrieve, rerank, and generate a citation-backed answer | V1 |
| `analyze_impact` | Find references/callers/imports/tests, explain likely change impact | V2 |
| `repository_summary` | Summarize repository structure, major modules, relationships | V2 |

## Non-Negotiable Demo Questions
Every answer must identify relevant file/line and clearly separate retrieved evidence from inference.
1. "Where is authentication handled?"
2. "Where is `createUser()` called?"
3. "Explain the login flow."
4. "What would break if I change `generateToken()`?" *(V2)*

## Roadmap

Status legend: `[ ]` not started · `[~]` in progress · `[x]` complete.

- [ ] **01 — Foundation** — `feature/foundation`
  Repo + README (MIT license) + `pyproject.toml`. Folder structure: ingestion, parser, chunker, retrieval, reranker, generation, citations, impact, mcp, tests. Lock stack per the table above. `.env.example` with all free API key slots. Architecture doc + V1 acceptance criteria.

- [ ] **02 — GitHub Ingestion & File Filtering** — `feature/github-ingestion`
  Accept a GitHub URL or local path, clone safely, detect languages, ignore `.git`/`node_modules`/`build`/`dist`/`coverage`/binaries/lockfiles/oversized files, return repo stats, ingestion tests.

- [x] **03 — Tree-sitter Parsing & AST Chunking** — `feature/tree-sitter-chunking`
  Parsers for TS/JS first (Python if time allows). Extract functions/classes/methods/interfaces. Chunk by code structure, not character count. Store `repo, file, symbol, type, language, start_line, end_line`. Safe fallback splitting for oversized functions.

- [ ] **04 — Embeddings & Vector Index** — `feature/embeddings-vector-index`
  Wire `all-MiniLM-L6-v2` via `langchain_huggingface.HuggingFaceEmbeddings`, store in FAISS, persistent index save/load, small retrieval test set.

- [ ] **05 — BM25 & Hybrid Retrieval** — `feature/hybrid-retrieval`
  `rank-bm25` index over chunks/symbols/paths. Combine BM25 + vector candidates via transparent scoring/reciprocal-rank. Test semantic vs. exact-symbol queries separately.

- [ ] **06 — Reranker & Retrieval Quality** — `feature/reranker`
  Cross-encoder reranks a larger hybrid candidate pool down to the strongest context. Log retrieval stages. Write 10–20 benchmark questions (doubles as the V2 eval set).

- [ ] **07 — LLM Generation & Citations** — `feature/llm-generation-citations`
  RAG generation through the NVIDIA NIM → Groq → OpenRouter fallback chain. Strict evidence-only prompt, file/line-formatted context, "not enough evidence" behavior. Test auth/API/DB/component questions.

- [ ] **08 — MCP Server (V1)** — `feature/mcp-server-v1`
  Official MCP SDK server exposing `search_code`, `find_symbol`, `get_file_context`, `repository_summary` (if stable). Clear tool schemas + error messages. Connect to Claude Code/Desktop over stdio, run end-to-end questions.

- [ ] **09 — V1 Hardening & Demo** — `feature/v1-hardening-demo`
  Fix retrieval/citation bugs, add logging, tests across ingestion/chunking/retrieval/citations/MCP tools, polish README (architecture diagram, setup, all free API key options), repeatable demo script, record the demo GIF. **Freeze V1.**

- [ ] **10 — Symbols, References & Impact Analysis (V2)** — `feature/symbol-impact-analysis`
  Symbol lookup from AST metadata, lightweight reference/import/caller model, direct-caller lookup, `analyze_impact(symbol)`, LLM used only after deterministic evidence is collected, label likely vs. confirmed impact.

- [ ] **11 — V2 Polish & Evaluation** — `feature/v2-polish-evaluation`
  `repository_summary` tool, improved impact-analysis prompts/citations, caching/incremental indexing, run the Day 6 benchmark set and fix highest-impact failures, security controls (path restriction, secret-file exclusion, prompt-injection-aware handling), CLI polish.

- [ ] **12 — Final QA & Open-Source Packaging** — `feature/final-packaging`
  Clean-clone install test, index the final demo repo, full test run, all four benchmark questions verified end-to-end with correct citations, finalize README/diagram/demo GIF, confirm MIT license, write resume bullets, tag `v1.0.0`.
  *(Note: the `day_number=12` slot in `.claude/specs/` was actually filed against zero-config auto-indexing, added on top of an already-further-along repo state, per an explicit user request — see `DECISIONS.md` D-026. This entry's original scope — clean-clone QA, README/demo polish, tagging `v1.0.0` — remains unbuilt.)*

- [ ] **13 — Cross-Agent MCP Packaging & Portability** — `feature/cross-agent-mcp-packaging-portability`
  Added post-plan-window per an explicit user request (see `DECISIONS.md`) — not part of the original 20–31 Aug plan. Make the stdio MCP server reliably launchable by *any* MCP-compatible client (Claude Code, Claude Desktop, Cursor, Windsurf, Cline, generic stdio hosts), not just the dev shell it was built and tested in: stop `DATA_DIR`/`INDEX_DIR`/`.env` resolution from silently depending on the launching client's working directory, verify `pip`/`pipx`/`uvx` install and launch, and publish ready-to-copy per-client config snippets.

*Optional add-ons, not separate roadmap days — fold into the relevant day above if time allows: swap FAISS → Qdrant (Day 4/12) for the stronger resume line; ship the fully-offline local-LLM mode via Ollama/MLX (Day 7/9).*

## What NOT to Build Before 31 August
- A large custom web frontend
- Multi-agent orchestration
- Kubernetes/microservices
- Custom model training
- Dozens of programming languages
- A full production-scale distributed vector database
- Perfect whole-program static analysis
- Complex authentication/user accounts
- A hosted multi-user MCP server with remote OAuth

## Priority Ladder
1. GitHub ingestion · 2. Tree-sitter AST chunking · 3. Vector retrieval (local embeddings + FAISS) · 4. BM25 retrieval · 5. Hybrid retrieval · 6. Reranking · 7. LLM + citations (multi-provider fallback) · 8. MCP integration · 9. Claude Code demo · 10. Symbol/reference analysis · 11. Impact analysis · 12. Evaluation + polish · 13. Open-source packaging · 14. Optional fully-offline local-LLM mode