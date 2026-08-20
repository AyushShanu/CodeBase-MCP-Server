# Codebase RAG MCP Server — Final Implementation Plan

**Target:** Production-ready Version 1 + strong Version 2 MVP by 31 August 2026
**Plan window:** 20–31 August 2026 (12 days)
**Distribution strategy:** Open-source, MIT license, clone-and-run (bring your own free API key) — no hosted multi-user service in this sprint
**Hardware:** MacBook (Apple M5, 16GB unified memory, 512GB SSD)

---

## 1. Project Goal

Build an AI developer assistant that indexes a real GitHub repository, understands code structure using Tree-sitter/AST-aware chunking, retrieves relevant code using hybrid BM25 + vector search, reranks the results, generates citation-backed answers, and exposes the capabilities through an MCP server so Claude Code / Claude Desktop can use them.

It ships as an open-source repo. Anyone can clone it, drop in one free API key, point it at a repo, and run it locally against their own Claude Code/Desktop — no signup with you, no hosted backend, no cost to you as the maintainer.

---

## 2. Final Architecture

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

Everything below the "Embeddings/BM25" layer runs **locally on your machine** except the final LLM generation call, which uses a free hosted API (with a fully local fallback option — see 3.1).

---

## 3. Tech Stack — at least 3 options per component

### 3.1 LLM (answer generation) — pick a fallback chain, not just one

| # | Option | Type | Free tier | Notes |
|---|--------|------|-----------|-------|
| 1 | **NVIDIA NIM (build.nvidia.com)** | Hosted API | No card required, ~40 req/min, 100+ hosted models (Llama, Nemotron, etc.) | **Recommended primary.** Real free tier, not a burn-down trial. |
| 2 | **Groq** | Hosted API | Free, very fast (LPU inference), Llama 3.x family, generous req/day | **Recommended secondary.** Best latency — good for live demos. |
| 3 | **OpenRouter** | Hosted API (router) | Free `:free` models — 50 req/day at $0 lifetime spend, 1,000/day if you've ever bought $10+ credit | **Recommended fallback.** Broadest model catalog if the other two are down/limited. |
| 4 | Google Gemini API | Hosted API | Free tier, no spend required, limits vary by model/tier | Optional 4th fallback. |
| 5 | Local Qwen2.5-Coder-7B-Instruct (Q4_K_M) via Ollama or MLX | Fully local | Unlimited, $0, works offline | Optional "zero API key, fully offline" bonus mode for the README. **Do not use Q2_XXS or a large model crushed to 2-bit** — quality degrades too much for a system whose whole value prop is grounded, citation-accurate answers. A smaller model (7B) at a sane quant (Q4/Q5) beats a huge model at 2-bit for this task. |

**Decision:** wire NVIDIA NIM → Groq → OpenRouter as an automatic fallback chain, reusing the provider-agnostic LLM layer pattern already used in ResearchOS. Ship local Qwen2.5-Coder-7B (Q4_K_M) as an optional fully-offline mode, not the default.

### 3.2 Embeddings — all run locally, no API, no rate limit

| # | Model | Size | Notes |
|---|-------|------|-------|
| 1 | `sentence-transformers/all-MiniLM-L6-v2` | ~22M params, ~80MB | **Use first**, on Day 4, to get the pipeline working fast. General-purpose, not code-specialized. |
| 2 | `jinaai/jina-embeddings-v2-base-code` | ~161M params, ~550MB | **Swap in before the final demo.** Code-aware — meaningfully better retrieval on "where is X handled"-style queries against real code + docstrings. |
| 3 | `BAAI/bge-small-en-v1.5` | ~130M params | Alternative general-purpose fallback if the code-specific model underperforms on your test repo. |

All three load via LangChain the same way — swapping is a one-line `model_name` change:

```python
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
```

This runs the model **locally** (downloads weights once from the HF Hub, then does inference on your own machine) — not a call to HF's hosted Inference API. Zero rate limits, zero network dependency once weights are cached.

### 3.3 Reranker — all run locally

| # | Model | Notes |
|---|-------|-------|
| 1 | `cross-encoder/ms-marco-MiniLM-L-6-v2` | **Recommended default.** Fast, well-tested, small. |
| 2 | `BAAI/bge-reranker-base` | Alternative — slightly heavier, strong accuracy. |
| 3 | `mixedbread-ai/mxbai-rerank-base-v1` | Alternative modern reranker if you want to compare. |

Use via `sentence-transformers.CrossEncoder`. Free, unlimited, no API key.

### 3.4 Vector Store

| # | Option | Notes |
|---|--------|-------|
| 1 | **FAISS** (local, in-process, via `langchain_community.vectorstores.FAISS`) | **Recommended default** — zero infra, zero signup, free forever. Use `faiss-cpu` on your Mac (no Metal GPU support in FAISS). |
| 2 | **Qdrant** (local Docker, or Qdrant Cloud free tier: 0.5 vCPU / 1GB RAM / 4GB disk, free forever) | Optional upgrade — a *named* vector database reads better on a resume than "a local file," and the LangChain integration is a similarly small swap. |
| 3 | **Chroma** (embedded, local) | Alternative simple option if you want a DX comparison point. |

**Decision:** start with FAISS to match the pipeline you already sketched; consider swapping to Qdrant (local or free cloud) before the final packaging pass if you want the stronger resume line — it's not required for V1 to function.

### 3.5 Keyword Search

`rank-bm25` (local Python library) — no real alternative needed here, it's free and does the job.

### 3.6 Parsing

Tree-sitter (`py-tree-sitter` + language grammars) — local, open source, free.

### 3.7 MCP Server

Official MCP Python SDK — local, open source, free.

### 3.8 Hardware fit check (M5, 16GB unified memory, 512GB SSD)

Rough RAM budget running everything at once (OS + Claude Desktop + embedding model + reranker + BM25/vector index in memory + MCP process): ~6–8GB baseline, leaving ~8–10GB of headroom.

- Local embedding/reranker models above: well under 1GB combined — no concern.
- If using the optional local LLM mode: Qwen2.5-Coder-7B at Q4_K_M ≈ 4.5–5GB — fits comfortably in that headroom.
- Avoid a 32B+ model at Q2_XXS (~9–10GB) — it technically fits numerically but the 2-bit quality loss undermines citation accuracy, which is the whole point of the demo.

---

## 4. Scope: Version 1 vs Version 2

| Version | Must Have | Outcome |
|---|---|---|
| **V1 — Core** | GitHub ingestion; file filtering; Tree-sitter AST parsing; AST-aware chunks; local embeddings; vector retrieval; BM25; hybrid retrieval; reranker; LLM answers via fallback chain; citations; MCP tools; Claude Code connection; tests and demo. | A working Codebase RAG MCP server that can answer code questions with file/line citations. |
| **V2 — Intelligence** | Symbol/reference search; dependency/call graph; impact analysis; repository summary; stronger citation handling; evaluation dataset; caching/index persistence; better error handling. | A differentiated developer assistant that can explain change impact and code relationships. |

---

## 5. Non-Negotiable MVP Demo

Use one real public GitHub repository. The final demo should show Claude Code asking:

- "Where is authentication handled?"
- "Where is `createUser()` called?"
- "Explain the login flow."
- "What would break if I change `generateToken()`?" (Version 2)

Every answer should identify relevant file paths and line ranges and clearly distinguish retrieved evidence from inference.

---

## 6. Day-by-Day Implementation Plan

### Day 1 — Thu, 20 Aug — Project setup + architecture
- Create Git repository and README (MIT license from day one).
- Create Python environment and dependency setup (`pyproject.toml`).
- Define folder structure: ingestion, parser, chunker, retrieval, reranker, generation, citations, impact, mcp, tests.
- Lock initial stack: Python, Tree-sitter, FAISS (`faiss-cpu`), `rank-bm25`, `all-MiniLM-L6-v2` embeddings, `ms-marco-MiniLM-L-6-v2` reranker, NVIDIA NIM/Groq/OpenRouter fallback chain, official MCP SDK.
- Set up `.env.example` with all three free API keys documented (`NVIDIA_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, optional `GEMINI_API_KEY`).
- Write a small architecture document and define V1 acceptance criteria.

*Done when: the project runs locally and a blank CLI/server skeleton is working.*

### Day 2 — Fri, 21 Aug — GitHub ingestion + file filtering
- Accept a GitHub URL or local repository path.
- Clone/download the repository safely.
- Detect supported languages.
- Ignore `.git`, `node_modules`, `build`/`dist`, `coverage`, binaries, lockfiles and oversized/generated files.
- Return repository statistics.
- Add ingestion tests.

*Done when: a real repo can be indexed into a clean list of source files.*

### Day 3 — Sat, 22 Aug — Tree-sitter parsing + AST-aware chunking
- Add parsers for the first target languages (start with TypeScript/JavaScript; add Python if time permits).
- Extract functions, classes, methods, interfaces and useful declarations.
- Create chunks around real code structures rather than character counts.
- Store metadata: repo, file, symbol, type, language, start_line, end_line.
- Handle oversized functions/classes with safe fallback splitting.

*Done when: every indexed chunk has coherent code boundaries and exact line metadata.*

### Day 4 — Sun, 23 Aug — Embeddings + vector index
- Wire up `all-MiniLM-L6-v2` via `langchain_huggingface.HuggingFaceEmbeddings` (fast baseline to unblock the pipeline).
- Generate embeddings for chunks; store vectors + metadata in FAISS.
- Implement semantic search.
- Add persistent index storage (save/load the FAISS index) so the repo isn't re-embedded every run.
- Create a small retrieval test set.

*Done when: semantic queries return relevant code chunks with file/line metadata.*

### Day 5 — Mon, 24 Aug — BM25 + hybrid retrieval
- Build a `rank-bm25` index over code chunks/symbol names/file paths.
- Implement exact keyword and symbol search.
- Combine BM25 and vector candidates with a transparent scoring/reciprocal-rank approach.
- Return top-N candidates with retrieval scores and metadata.
- Test semantic queries and exact-symbol queries separately.

*Done when: both exact and semantic code searches work and hybrid search beats either method alone on your small test set.*

### Day 6 — Tue, 25 Aug — Reranker + retrieval quality
- Add the cross-encoder reranker (`ms-marco-MiniLM-L-6-v2` default).
- Retrieve a larger candidate pool from hybrid search, rerank against the user query, keep only the strongest context.
- Log retrieval stages so you can debug poor results.
- Create 10–20 benchmark questions against the selected repository (this becomes your V2 evaluation set too).

*Done when: the system can show query → candidates → reranked evidence.*

### Day 7 — Wed, 26 Aug — LLM answer generation + citations
- Build the RAG generation pipeline against the NVIDIA NIM → Groq → OpenRouter fallback chain.
- Create a strict prompt requiring answers to rely on retrieved evidence only.
- Format source context with file path and line range.
- Generate concise answers with citations; add "not enough evidence" behavior.
- Test authentication, API, database and component questions.

*Done when: the assistant gives useful answers with accurate file/line citations, and automatically fails over between providers if one is rate-limited.*

### Day 8 — Thu, 27 Aug — MCP server (Version 1)
- Implement the MCP server (official Python SDK).
- Expose `search_code`, `find_symbol`, `get_file_context`, and `repository_summary` if stable.
- Add clear tool schemas and error messages.
- Connect the MCP server to Claude Code/Claude Desktop (local stdio transport).
- Run end-to-end questions through Claude.

*Done when: Claude can call your MCP tools and answer questions about the indexed repository.*

### Day 9 — Fri, 28 Aug — Version 1 hardening + demo
- Fix retrieval and citation bugs.
- Add indexing/query logging.
- Add tests for ingestion, chunking, retrieval, citations and MCP tools.
- Improve README: architecture diagram, setup steps, all free API key options, usage instructions.
- Create a repeatable demo repository/indexing script.
- Record or rehearse the V1 demo (this becomes your demo GIF for the README/LinkedIn).
- Freeze Version 1.

*Done when: V1 is stable enough to show publicly and another developer can run it from the README alone, using only free API keys.*

### Day 10 — Sat, 29 Aug — Version 2: symbols + references + impact analysis
- Implement symbol lookup from the AST metadata.
- Build a lightweight reference/import/caller relationship model.
- Find direct callers/usages of a symbol.
- Implement `analyze_impact(symbol)`.
- Return affected files, references and tests.
- Use the LLM only after collecting deterministic evidence.
- Clearly label likely impact vs. confirmed references.

*Done when: `generateToken`/`createUser` can be traced to callers and an impact report can be generated.*

### Day 11 — Sun, 30 Aug — Version 2 polish + evaluation
- Add repository overview/architecture summary (`repository_summary` tool).
- Improve impact-analysis prompts and citations.
- Add caching and incremental indexing where practical.
- Run the benchmark questions from Day 6 and record retrieval/citation failures.
- Fix the highest-impact errors.
- Add security controls: path restrictions, secret-file exclusions, prompt-injection-aware handling of repository text.
- Polish CLI/tool responses.

*Done when: Version 2 feels like a developer tool rather than a basic chatbot.*

### Day 12 — Mon, 31 Aug — Final QA + open-source packaging
- Run a clean installation from scratch (simulate a stranger cloning the repo).
- Index the final real GitHub repository.
- Run all automated tests.
- Perform the four core demo questions.
- Verify every displayed citation points to the correct file/line.
- Finalize README, architecture diagram, and screenshots/demo GIF.
- Confirm MIT license file is present and correct.
- Write resume/project description and key technical bullets.
- Tag/release the final version (e.g., `v1.0.0`) on GitHub.

*Done when: the project is reproducible from a clean clone, demoable, documented, open-sourced, and ready to submit/showcase.*

---

## 7. Recommended Repository Structure

```
codebase-rag-mcp/
├── src/
│   ├── ingestion/
│   ├── parser/
│   ├── chunker/
│   ├── indexing/
│   │   ├── vector.py
│   │   └── bm25.py
│   ├── retrieval/
│   ├── reranker/
│   ├── generation/
│   │   └── providers/        # NVIDIA NIM, Groq, OpenRouter, Gemini, local Ollama/MLX
│   ├── citations/
│   ├── impact/
│   ├── mcp/
│   └── cli/
├── tests/
├── scripts/
├── data/
├── README.md
├── LICENSE                   # MIT
├── pyproject.toml
└── .env.example              # all free API key slots documented
```

---

## 8. MCP Tool Contract

| Tool | Purpose |
|---|---|
| `search_code` | Search the repository using hybrid retrieval and return ranked code evidence. |
| `find_symbol` | Find a function/class/method/interface and its definition/relevant usages. |
| `get_file_context` | Return an exact file/line range for additional context. |
| `analyze_impact` | *Version 2:* find references/callers/imports/tests and explain likely change impact. |
| `repository_summary` | *Version 2:* summarize repository structure, major modules and relationships. |

---

## 9. Definition of Done

- A real GitHub repository can be indexed.
- Code is chunked by AST/code structure rather than naive fixed-size text splitting.
- Every chunk preserves file path and line metadata.
- Vector and BM25 retrieval both work.
- Hybrid retrieval is implemented.
- A reranker improves/filters retrieved candidates.
- The LLM answers from retrieved evidence, with automatic fallback across at least 2 free providers.
- Answers contain usable file/line citations.
- MCP tools are callable from Claude Code/Claude Desktop.
- Version 2 can trace symbol references and provide change-impact analysis.
- Tests cover the core pipeline.
- README allows another developer to reproduce the demo using only free API keys — no paid service required.
- Repo is public, MIT-licensed, and includes a demo GIF.

---

## 10. Distribution Strategy: Open-Source, Clone-and-Run

- **License:** MIT — maximizes visibility and reuse, standard for portfolio projects.
- **Model:** bring-your-own-API-key. Every provider in the stack (NVIDIA NIM, Groq, OpenRouter, Gemini) has a genuine free tier — nobody who clones this needs to pay to try it.
- **No hosted multi-user service in this sprint.** Running a public hosted MCP server means you personally absorb every visitor's LLM/embedding costs, plus real auth, rate-limiting, and remote-MCP-OAuth work — that's explicitly out of scope for 20–31 Aug (see Section 11).
- **README must include:** one-command setup, architecture diagram, the four benchmark demo questions with real output, a short demo GIF/video showing it answering live inside Claude Code, and a clear "free API keys only" setup section.
- **Future stretch (not this sprint):** a single self-hosted, rate-limited public demo over one pre-indexed repo, so people without a dev environment can see it work before cloning. Explicitly deferred — do not start this before 31 Aug.

---

## 11. What NOT to Build Before 31 August

- A large custom web frontend.
- Multi-agent orchestration.
- Kubernetes/microservices.
- Custom model training.
- Dozens of programming languages.
- A full production-scale distributed vector database.
- Perfect whole-program static analysis.
- Complex authentication/user accounts.
- A hosted multi-user MCP server with remote OAuth (explicitly deferred — see Section 10).

**Priority rule:** working V1 > polished V2 > extra features. If you fall behind, cut features rather than compromising the core retrieval + MCP demo.

---

## 12. Daily Working Pattern

- First 30–45 min: understand the day's goal and inspect yesterday's output.
- Main block: implement only that day's feature.
- Second block: test it against the real repository.
- Final 30 min: clean code, commit, update README/project notes and record what is working/broken.
- Do not move to the next stage until the current stage has a small working test.

---

## 13. Resume & LinkedIn Positioning

**Resume bullet:**
Built an AST-aware Codebase RAG MCP Server that indexes GitHub repositories using Tree-sitter, hybrid BM25/vector retrieval and reranking, then exposes citation-backed code search, symbol lookup and change-impact analysis directly to Claude Code — with automatic fallback across multiple free LLM providers and a fully self-contained, open-source, clone-and-run design.

**LinkedIn angle:** a short demo GIF/video is worth more than a text post here — show Claude Code asking one of the four benchmark questions live and getting back a cited, file/line-accurate answer, then a quick cut to the same tool answering with zero external LLM (local mode) to show it also works fully offline.

---

## 14. Final Priority Ladder

1. GitHub ingestion
2. Tree-sitter AST chunking
3. Vector retrieval (local embeddings + FAISS)
4. BM25 retrieval
5. Hybrid retrieval
6. Reranking
7. LLM + citations (with multi-provider fallback)
8. MCP integration
9. Claude Code demo
10. Symbol/reference analysis
11. Impact analysis
12. Evaluation + polish
13. Open-source packaging (README, MIT license, demo GIF)
14. Optional: fully offline local-LLM mode