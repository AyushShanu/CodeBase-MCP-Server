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

Ingestion (`ingestion/loader.py` → `ingestion/filters.py` /
`ingestion/languages.py` → `ingestion/scanner.py`) accepts either an
`https://` GitHub URL — shallow-cloned via `subprocess` + the system
`git` into `DATA_DIR/clones/` — or a local directory path, then walks
the checkout applying ignore rules and extension-based language
detection to produce the `RepoStats` / `FileRecord` list consumed by
the parser stage below.

Parsing and chunking (`parser/extractor.py:parse_file` →
`chunker/chunker.py:chunk_file`, using `chunker/fallback.py:
split_oversized_symbol` for any symbol whose span exceeds
`DEFAULT_MAX_CHUNK_LINES`) each operate on **one file at a time** — given
a `FileRecord.path`/`language` and that file's raw bytes, `parse_file`
resolves a Tree-sitter grammar (`parser/grammars.py`, `.tsx` routed to
the `"tsx"` grammar even though ingestion reports it as `"typescript"`)
and hand-walks the AST into a `ParseResult` (functions/classes/methods/
interfaces with 1-indexed line ranges, qualified `ClassName.method`
names for nested symbols); `chunk_file` then decodes the file's bytes to
text exactly once and slices one `Chunk` per symbol (or a single
whole-file fallback `Chunk` if a file has none). **There is no
repo-wide orchestration here yet** — looping `parse_file`/`chunk_file`
over every `FileRecord` in a `RepoStats` and aggregating the resulting
chunks into one collection is Day 04's job (the embedding stage is the
first consumer that actually needs "all chunks for the repo" as a single
collection).

```mermaid
flowchart TD
    A[Target: https:// GitHub URL or local path] --> B1[ingestion.loader<br/>load_repo]
    B1 --> B2{https URL or local path?}
    B2 -->|https URL| B3[git clone --depth 1<br/>subprocess, timeout-enforced]
    B2 -->|local path| B4[validate exists + is directory]
    B3 --> B5[ingestion.scanner<br/>scan]
    B4 --> B5
    B5 --> B6[ingestion.filters<br/>dir / lockfile / binary / size rules]
    B5 --> B7[ingestion.languages<br/>extension to language]
    B6 --> B8[RepoStats + FileRecord list]
    B7 --> B8
    B8 --> C1[parser.grammars<br/>resolve_grammar_name + cached Parser]
    C1 --> C2[parser.extractor<br/>parse_file: Tree-sitter AST walk]
    C2 --> D1[chunker.chunker<br/>chunk_file: decode once, slice per symbol]
    D1 -.oversized symbol.-> D2[chunker.fallback<br/>split_oversized_symbol]
    D2 -.-> D1
    D1 --> E[indexing.vector<br/>FAISS + embeddings]
    D1 --> F[indexing.bm25<br/>rank-bm25]
    E --> G[(data/index/)]
    F --> G
    D1 --> H[(chunk metadata)]
    H --> G
```

`parse_file`/`chunk_file` are exercised directly by
`tests/test_parser.py`/`tests/test_chunker.py` and a manual smoke test
today — the `E`/`F`/`G` boxes above (embedding, BM25, persisted indexes)
remain aspirational until Day 04/05 land the repo-wide loop that actually
calls them.

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
