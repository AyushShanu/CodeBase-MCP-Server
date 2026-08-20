"""Tests for the MCP server stub.

These tests exercise the in-process server and assert that it advertises
the placeholder ``ping`` tool and responds to a ``tools/call`` for it.
We deliberately avoid the stdio transport (which needs a real parent
process and is covered separately).
"""

from __future__ import annotations

import pytest

from codebase_rag_mcp.mcp.server import _build_server


@pytest.mark.asyncio
async def test_server_advertises_ping_tool() -> None:
    server = _build_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "ping" in names


@pytest.mark.asyncio
async def test_call_tool_ping_returns_pong() -> None:
    server = _build_server()
    result = await server.call_tool("ping", {})
    assert result
    first = result[0]
    text = getattr(first, "text", None)
    assert text is not None
    assert text.startswith("pong")


@pytest.mark.asyncio
async def test_call_tool_unknown_raises() -> None:
    server = _build_server()
    with pytest.raises(ValueError, match="Unknown tool"):
        await server.call_tool("does_not_exist", {})
