"""MCP server entrypoint: exposes RAG tools over the Model Context Protocol.

:func:`run` boots a real `mcp.server.MCPServer` (V1) exposing four tools --
``search_code``, ``find_symbol``, ``get_file_context``, ``ask`` -- backed by
the full ingestion/chunking/indexing/retrieval/reranking/generation
pipeline. See ``mcp.server`` and DECISIONS.md D-023.
"""

from codebase_rag_mcp.mcp.server import run

__all__ = ["run"]
