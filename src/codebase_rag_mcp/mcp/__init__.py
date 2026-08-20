"""MCP server entrypoint: exposes RAG tools over the Model Context Protocol.

Not implemented yet beyond a stub :func:`run` that boots a server exposing
a single placeholder ``ping`` tool. Real tools (``search``, ``impact``,
``ask``, etc.) will be added as the rest of the pipeline lands.
"""

from codebase_rag_mcp.mcp.server import run

__all__ = ["run"]
