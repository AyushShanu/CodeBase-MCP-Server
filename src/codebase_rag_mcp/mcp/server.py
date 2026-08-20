"""MCP server stub.

Boots a stdio MCP server and registers a single placeholder ``ping``
tool so the scaffold can be exercised end-to-end (e.g. ``codebase-rag
serve`` will respond to ``list_tools`` and ``tools/call`` for ``ping``).

This deliberately does not implement any RAG pipeline yet. Real tools
(``search``, ``impact``, ``ask``...) will be registered here as the
ingestion, indexing, retrieval, and generation modules land.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool

logger = logging.getLogger(__name__)

# A short, fixed name for the MCP server. Clients use this to identify
# the integration.
SERVER_NAME = "codebase-rag-mcp"

# Version is read from the package so ``codebase-rag --version`` and the
# MCP initialize handshake agree.
try:
    from codebase_rag_mcp import __version__ as _VERSION
except Exception:  # pragma: no cover - defensive only
    _VERSION = "0.0.0"


def _build_server() -> Server:
    """Create and configure the MCP server with its tool registry."""
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        """Advertise a single placeholder ``ping`` tool."""
        return [
            Tool(
                name="ping",
                description=(
                    "Placeholder liveness tool for the codebase-rag-mcp scaffold. "
                    "Returns 'pong'. Real RAG tools will be added as the pipeline lands."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        """Dispatch a tool invocation. Only ``ping`` is implemented."""
        if name == "ping":
            text = f"pong (codebase-rag-mcp {_VERSION})"
            # The MCP Python SDK accepts a list of content parts; the
            # simplest portable shape is a TextContent-bearing list.
            from mcp.types import TextContent

            return [TextContent(type="text", text=text)]

        raise ValueError(f"Unknown tool: {name!r}")

    return server


async def _serve() -> None:
    """Run the MCP server over stdio until the parent closes the pipe."""
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run() -> None:
    """Synchronous entrypoint used by the CLI's ``serve`` subcommand."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s v%s (scaffold stub)", SERVER_NAME, _VERSION)
    asyncio.run(_serve())


__all__ = ["SERVER_NAME", "run"]
