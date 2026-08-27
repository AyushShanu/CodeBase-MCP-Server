"""Local OpenAI-compatible server adapter -- Ollama, vLLM, LM Studio, llama.cpp server.

This is CLAUDE.md's fully-offline mode's entry point (e.g. Qwen2.5-Coder-7B-
Instruct Q4_K_M served locally): a zero-code-change fifth OpenAI-shaped
adapter pointed at a user-supplied `LOCAL_MODEL_BASE_URL`.
"""

from __future__ import annotations

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.providers._openai_compatible import OpenAICompatibleProvider


class LocalProvider(OpenAICompatibleProvider):
    """`Provider` for a user-run OpenAI-compatible local server."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            name="local",
            base_url=config.LOCAL_MODEL_BASE_URL,
            api_key=config.LOCAL_MODEL_API_KEY,
            model_name=config.LOCAL_MODEL_NAME,
            transport=transport,
        )


__all__ = ["LocalProvider"]
