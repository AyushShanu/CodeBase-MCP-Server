"""Generation: prompt construction and provider-agnostic LLM calls.

`generation.pipeline.generate_answer` is the stage entry point: query +
Day 06's reranked evidence in, a citation-backed `GeneratedAnswer` out,
routed through the NVIDIA -> Groq -> OpenRouter -> Gemini -> Local fallback
chain (`generation.providers`) with runtime fallback-on-failure.

Subpackages:

- :mod:`codebase_rag_mcp.generation.providers` -- NVIDIA, Groq,
  OpenRouter, Gemini, and local OpenAI-compatible providers.
"""

from __future__ import annotations

from codebase_rag_mcp.generation import providers
from codebase_rag_mcp.generation.exceptions import (
    AllProvidersFailedError,
    GenerationError,
    NoProviderConfiguredError,
    ProviderRequestError,
)
from codebase_rag_mcp.generation.models import GeneratedAnswer
from codebase_rag_mcp.generation.pipeline import generate_answer

__all__ = [
    "AllProvidersFailedError",
    "GeneratedAnswer",
    "GenerationError",
    "NoProviderConfiguredError",
    "ProviderRequestError",
    "generate_answer",
    "providers",
]
