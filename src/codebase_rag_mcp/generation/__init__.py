"""Generation: prompt construction and provider-agnostic LLM calls.

Subpackages:

- :mod:`codebase_rag_mcp.generation.providers` -- NVIDIA, Groq,
  OpenRouter, Gemini, and local OpenAI-compatible providers.
"""

from codebase_rag_mcp.generation import providers

__all__ = ["providers"]
