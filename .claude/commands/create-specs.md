---
description: Create a spec file and feature branch for the next Codebase RAG MCP Server build day
argument-hint: "Day number and feature name e.g. 3 tree-sitter-chunking"
allowed-tools: Read, Write, Glob, Bash(git:*)
---

You are a senior AI/ML engineer spinning up the next build step for
the Codebase RAG MCP Server — an AST-aware, hybrid-retrieval RAG
system exposed to Claude Code/Claude Desktop over MCP. Always follow
the rules in CLAUDE.md.

User input: $ARGUMENTS

## Step 1 — Check working directory is clean
Run `git status` and check for uncommitted, unstaged, or
untracked files. If any exist, stop immediately and tell
the user to commit or stash changes before proceeding.
DO NOT CONTINUE until the working directory is clean.

## Step 2 — Parse the arguments
From $ARGUMENTS extract:

1. `day_number` — zero-padded to 2 digits: 3 → 03, 12 → 12
   (matches the day-by-day plan in CLAUDE.md — Day 1 = Foundation
   through Day 12 = Final QA + packaging)

2. `feature_title` — human readable title in Title Case
   - Example: "Tree-sitter Parsing & AST Chunking" or "MCP Server (Version 1)"

3. `feature_slug` — git and file safe slug
   - Lowercase, kebab-case
   - Only a-z, 0-9 and -
   - Maximum 40 characters
   - Example: tree-sitter-chunking, mcp-server-v1

4. `branch_name` — format: `feature/<feature_slug>`
   - Example: `feature/tree-sitter-chunking`

If you cannot infer these from $ARGUMENTS, ask the user
to clarify before proceeding.

## Step 3 — Check branch name is not taken
Run `git branch` to list existing branches.
If `branch_name` is already taken, append a number:
`feature/tree-sitter-chunking-01`, `-02` etc.

## Step 4 — Switch to main and pull latest
Run:
```
git checkout main
git pull origin main
```

## Step 5 — Create and switch to the feature branch
Run:
```
git checkout -b <branch_name>
```

## Step 6 — Research the codebase
Read these before writing the spec:
- `CLAUDE.md` — roadmap, architecture, stack options, conventions
- `DECISIONS.md` — prior architecture/library decisions and their reasoning; do not silently re-decide something already settled here (e.g. which embedding model, which vector store) without flagging it to the user
- `FLOW.md` — how data currently travels through the pipeline (ingestion → parsing → chunking → embedding → retrieval → rerank → generation → citations → MCP); understand what this day's feature plugs into before adding to it
- The `src/` subfolder(s) relevant to this day's pipeline stage
- All files in `.claude/specs/` — avoid duplicating existing specs

Check `CLAUDE.md`'s roadmap to confirm the requested day is not
already marked complete. If it is, warn the user and stop.

## Step 7 — Bootstrap DECISIONS.md and FLOW.md if missing
If `DECISIONS.md` does not exist at the repo root, create it with:
```
# Decisions

Log every meaningful decision made while building the Codebase RAG
MCP Server, and the reasoning behind it — model/library choices
(e.g. which of the 3+ options in CLAUDE.md was picked and why),
patterns, accepted tradeoffs. Newest entries at the top.

## Format
### <date> — <short decision title> (day-<day_number>-<feature_slug>)
**Decision:** what was chosen
**Why:** the reasoning
**Alternatives considered:** what else was on the table (reference the
CLAUDE.md options table where relevant) and why it lost
```

If `FLOW.md` does not exist at the repo root, create it with:
```
# Flow

Documents how data actually travels through the RAG pipeline —
which module/function calls which, in what order. Keep this scoped
to the real pipeline stages (ingestion → parsing → chunking →
embedding/BM25 → hybrid retrieval → reranking → generation →
citations → MCP tool response), not every helper function. Update
it whenever a spec changes or adds to a stage below.

## Core flows
(add flows here as they are built, e.g. "search_code MCP tool:
mcp/server.py:search_code → retrieval/hybrid.py:hybrid_search →
reranker/rerank.py:rerank → generation/pipeline.py:generate_answer
→ citations/verify.py:attach_citations → MCP response")
```

## Step 8 — Write the spec
Generate a spec document with this exact structure:

---
# Spec: <feature_title>

## Overview
One paragraph describing what this build day adds to the RAG
pipeline and why it belongs at this point in the roadmap.

## Depends on
Which previous days/pipeline stages this feature requires to be
complete before it can be built.

## Pipeline stage(s) touched
Which of Ingestion / Parsing & Chunking / Embedding / BM25 / Hybrid
Retrieval / Reranking / Generation / Citations / MCP Server / Impact
Analysis this day adds to or changes.

## MCP tools affected
Which of `search_code`, `find_symbol`, `get_file_context`,
`analyze_impact`, `repository_summary` are added or changed.
If none: state "No MCP tool changes".

## Model / provider choice for this step
If this day touches the LLM, embedding, reranker, or vector-store
layer: state which of the 3+ options in CLAUDE.md's stack table is
being used first, and note the others as documented alternatives
(don't silently pick one — name it and say why).

## Files to change
Every file that will be modified.

## Files to create
Every new file that will be created.

## New dependencies
Any new pip packages, and whether a new free API key is required
(name it and confirm it has a genuine free tier per CLAUDE.md).
If none: state "No new dependencies".

## Rules for implementation
Specific constraints Claude must follow. Always include:
- AST-aware chunking only — never fixed-size/character-count splitting for code
- Every retrieved chunk and every citation carries exact file path + start/end line metadata
- LLM answers must rely only on retrieved evidence; implement an explicit "not enough evidence" fallback rather than letting the model guess
- All LLM calls go through the provider fallback chain defined in CLAUDE.md — never hardcode a single provider
- Structured outputs via Pydantic for any LLM call that isn't freeform prose
- Embedding and reranker models run locally by default — no paid API in the default path
- Log every meaningful decision to `DECISIONS.md`
- Update `FLOW.md` with any new or changed pipeline path

## Definition of done
A specific testable checklist. Each item must be verifiable by
running the pipeline/MCP server against the real demo repository.
---

## Step 9 — Save the spec
Save to: `.claude/specs/<day_number>-<feature_slug>.md`

## Step 10 — Report to the user
Print a short summary in this exact format:
```
Branch:    <branch_name>
Spec file: .claude/specs/<day_number>-<feature_slug>.md
Title:     <feature_title>
DECISIONS.md / FLOW.md: created | already present
```

Then tell the user:
"Review the spec at `.claude/specs/<day_number>-<feature_slug>.md`
then enter Plan Mode with Shift+Tab twice to begin implementation.
Remember to log decisions to DECISIONS.md and update FLOW.md as you build."

Do not print the full spec in chat unless explicitly asked.