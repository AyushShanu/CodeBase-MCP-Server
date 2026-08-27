"""Groq adapter -- an `OpenAICompatibleProvider` fixed to Groq's catalog."""

from __future__ import annotations

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.providers._openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    """`Provider` for Groq's OpenAI-compatible endpoint."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=config.GROQ_API_KEY,
            model_name=config.GROQ_MODEL_NAME,
            transport=transport,
        )


__all__ = ["GroqProvider"]
