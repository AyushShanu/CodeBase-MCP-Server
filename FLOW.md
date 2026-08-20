# FLOW

> End-to-end data flow for `codebase-rag-mcp`. Diagrams are intentionally
> Mermaid so they render in any Markdown viewer, GitHub, or VS Code.

---

## 1. Component map

```mermaid
flowchart LR
    subgraph Client
        IDE[IDE / MCP client]
    end

    subgraph Server[codebase-rag-mcp server]
        MCP[MCP stdio layer<br/>mcp/server.py]
        TOOLS[Tool handlers<br/>search · impact · ask]
        RETRIEVAL[Retrieval]
        RERANKER[Reranker]
        GEN[Generation]
    end

    subgraph Indexes[Persisted indexes]
        FAISS[(FAISS vector index)]
        BM25[(BM25 sparse index)]
        META[(Chunk metadata store)]
    end

    subgraph Providers[LLM providers]
        NVIDIA[NVIDIA]
        GROQ[Groq]
        OR[OpenRouter]
        GEM[Gemini]
        LOCAL[Local OpenAI-compatible]
    end

    IDE <-->|JSON-RPC over stdio| MCP
    MCP --> TOOLS
    TOOLS --> RETRIEVAL
    RETRIEVAL --> RERANKER
    RETRIEVAL --> FAISS
    RETRIEVAL --> BM25
    RETRIEVAL --> META
    RERANKER --> GEN
    GEN --> NVIDIA
    GEN --> GROQ
    GEN --> OR
    GEN --> GEM
    GEN --> LOCAL
```

---

## 2. Offline build flow

`codebase-rag index <path>` will walk the corpus once, persist indexes
to `INDEX_DIR`, and exit. Subsequent `codebase-rag serve` invocations
load the persisted indexes instead of rebuilding them.

```mermaid
flowchart TD
    A[Target codebase path] --> B[ingestion<br/>file discovery]
    B --> C[parser<br/>tree-sitter AST]
    C --> D[chunker<br/>AST-aware splits]
    D --> E[indexing.vector<br/>FAISS + embeddings]
    D --> F[indexing.bm25<br/>rank-bm25]
    E --> G[(data/index/)]
    F --> G
    D --> H[(chunk metadata)]
    H --> G
```

---

## 3. Online query flow (`ask` tool)

```mermaid
sequenceDiagram
    participant U as User (via MCP client)
    participant M as MCP server
    participant R as Retrieval
    participant X as Reranker
    participant L as LLM provider

    U->>M: tools/call { name: "ask", arguments: {query} }
    M->>R: hybrid_search(query, top_k)
    R-->>R: ANN over FAISS + BM25
    R-->>M: candidates (chunks + scores)
    M->>X: rerank(query, candidates)
    X-->>M: top_n chunks
    M->>L: chat(system, {query + cited chunks})
    L-->>M: answer + cited chunk IDs
    M-->>U: {answer, citations}
```

---

## 4. Provider selection

```mermaid
flowchart TD
    Cfg[Read .env] --> K1{NVIDIA_API_KEY set?}
    K1 -- yes --> P1[NVIDIA adapter]
    K1 -- no --> K2{GROQ_API_KEY set?}
    K2 -- yes --> P2[Groq adapter]
    K2 -- no --> K3{OPENROUTER_API_KEY set?}
    K3 -- yes --> P3[OpenRouter adapter]
    K3 -- no --> K4{GEMINI_API_KEY set?}
    K4 -- yes --> P4[Gemini adapter]
    K4 -- no --> K5{LOCAL_MODEL_NAME set?}
    K5 -- yes --> P5[Local OpenAI-compatible adapter]
    K5 -- no --> X[Error: no provider configured]
```

Selection order is configurable in a future iteration; the precedence
above keeps the most-specific provider keys first so an accidentally-
populated local block does not silently override a paid provider.

---

## 5. Data lifecycle

| Stage         | Lives where                            | Owner            |
| ------------- | -------------------------------------- | ---------------- |
| Raw source    | User's filesystem                      | User             |
| Parsed AST    | Memory only during ingest              | `parser/`        |
| Chunks        | Memory + serialized to `INDEX_DIR`     | `chunker/`       |
| Vector index  | `INDEX_DIR/*.faiss`                    | `indexing/vector.py` |
| BM25 index    | `INDEX_DIR/*.pkl`                      | `indexing/bm25.py`   |
| Logs          | stderr / `LOG_LEVEL`                   | `cli`, `mcp`     |
| Caches        | `.cache/`, `.mypy_cache/`, `.ruff_cache/` (gitignored) | tooling  |

Anything in `data/` or matching `*.faiss` / `*.index` is gitignored —
indexes are reproducible from source and should not be committed.
