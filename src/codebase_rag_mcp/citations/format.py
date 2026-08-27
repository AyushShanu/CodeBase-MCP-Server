"""Markdown rendering of a `Citation` list, grouped by file.

Available so Day 08's MCP tool responses (and this day's own manual smoke
test) can render citations consistently without reimplementing the same
grouping/sorting logic at each call site.
"""

from __future__ import annotations

from collections import defaultdict

from codebase_rag_mcp.citations.models import Citation


def format_citations_markdown(citations: list[Citation]) -> str:
    """Render `citations` as a Markdown bullet list grouped by file.

    Citations are grouped by `file`, files are sorted alphabetically, and
    citations within a file are sorted by `start_line`. Each citation renders
    as a single bullet: `` `file:start-end` (symbol) ``. Returns an empty
    string for an empty `citations` list.
    """
    if not citations:
        return ""

    by_file: dict[str, list[Citation]] = defaultdict(list)
    for citation in citations:
        by_file[citation.file].append(citation)

    lines: list[str] = []
    for file in sorted(by_file):
        for citation in sorted(by_file[file], key=lambda c: c.start_line):
            lines.append(
                f"- `{citation.file}:{citation.start_line}-{citation.end_line}` ({citation.symbol})"
            )
    return "\n".join(lines)


__all__ = ["format_citations_markdown"]
