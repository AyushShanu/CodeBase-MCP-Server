"""Citations: deterministic references from chunks back to source locations.

`citations.attach.attach_citations` maps the chunk IDs a generation-stage LLM
call reports using back to this project's own indexed chunk metadata --
never to anything the model itself asserts about file/line/symbol -- and
`citations.format.format_citations_markdown` renders the result as grouped
Markdown for display.
"""

from __future__ import annotations

from codebase_rag_mcp.citations.attach import attach_citations
from codebase_rag_mcp.citations.format import format_citations_markdown
from codebase_rag_mcp.citations.models import Citation

__all__ = ["Citation", "attach_citations", "format_citations_markdown"]
