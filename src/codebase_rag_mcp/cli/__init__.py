"""CLI entrypoint built on ``argparse``.

Public surface: :func:`main`. Registered as the ``codebase-rag`` script
in ``pyproject.toml``.
"""

from codebase_rag_mcp.cli.main import main

__all__ = ["main"]
