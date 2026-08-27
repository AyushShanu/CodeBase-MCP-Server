"""OpenRouter adapter -- an `OpenAICompatibleProvider` fixed to OpenRouter's catalog."""

from __future__ import annotations

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.providers._openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """`Provider` for OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=config.OPENROUTER_API_KEY,
            model_name=config.OPENROUTER_MODEL_NAME,
            transport=transport,
        )


__all__ = ["OpenRouterProvider"]
