"""MCP server entrypoint: exposes RAG tools over the Model Context Protocol.

`mcp.server`'s `run()` boots a real `mcp.server.MCPServer` exposing five
tools -- ``search_code``, ``find_symbol``, ``get_file_context``, ``ask``,
``analyze_impact`` -- backed by the full ingestion/chunking/indexing/
retrieval/reranking/generation/impact-analysis pipeline. See
``mcp.server`` and DECISIONS.md D-023/D-024.

Deliberately does NOT re-export `run` here (`from codebase_rag_mcp.mcp
import run` no longer works -- import `codebase_rag_mcp.mcp.server.run`
directly, as `cli/main.py` does). Since Day 10, `mcp.server` imports
`impact.analyzer`, and `impact.models` imports `mcp.models` -- eagerly
importing `mcp.server` here (a package `__init__.py`, which Python always
executes before any of its submodules, including `mcp.models`) would
create a real circular import the moment anything imports `impact.models`
before `mcp.server` has finished loading. Keeping this `__init__.py`
import-free avoids that entirely.
"""
