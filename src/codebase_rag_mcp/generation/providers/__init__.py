"""LLM providers: NVIDIA, Groq, OpenRouter, Gemini, and local OpenAI-compatible.

`select_providers` returns the configured subset of the five adapters below
in the project's fixed fallback-chain precedence order; `generation.pipeline
.generate_answer` is what actually iterates them with runtime
fallback-on-failure -- see DECISIONS.md D-022 for why those two mechanisms
are kept separate.
"""

from __future__ import annotations

from codebase_rag_mcp.generation.providers.base import Provider
from codebase_rag_mcp.generation.providers.gemini import GeminiProvider
from codebase_rag_mcp.generation.providers.groq import GroqProvider
from codebase_rag_mcp.generation.providers.local import LocalProvider
from codebase_rag_mcp.generation.providers.nvidia import NvidiaProvider
from codebase_rag_mcp.generation.providers.openrouter import OpenRouterProvider
from codebase_rag_mcp.generation.providers.registry import select_providers

__all__ = [
    "GeminiProvider",
    "GroqProvider",
    "LocalProvider",
    "NvidiaProvider",
    "OpenRouterProvider",
    "Provider",
    "select_providers",
]
