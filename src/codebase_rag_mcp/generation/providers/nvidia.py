"""NVIDIA NIM adapter -- an `OpenAICompatibleProvider` fixed to NVIDIA's catalog."""

from __future__ import annotations

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.providers._openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    """`Provider` for NVIDIA NIM's OpenAI-compatible `build.nvidia.com` endpoint."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(
            name="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=config.NVIDIA_API_KEY,
            model_name=config.NVIDIA_MODEL_NAME,
            transport=transport,
        )


__all__ = ["NvidiaProvider"]
