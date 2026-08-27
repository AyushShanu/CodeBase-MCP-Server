"""Config-time provider selection -- which candidates exist, in what order.

`select_providers` decides which providers are even candidates, purely from
which API keys/settings are configured, in the fixed NVIDIA -> Groq ->
OpenRouter -> Gemini -> Local precedence order (see FLOW.md Section 4). It
has no awareness of runtime success/failure -- that is
`generation.pipeline.generate_answer`'s job, kept as a deliberately separate
mechanism (see DECISIONS.md D-022).
"""

from __future__ import annotations

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.exceptions import NoProviderConfiguredError
from codebase_rag_mcp.generation.providers.base import Provider
from codebase_rag_mcp.generation.providers.gemini import GeminiProvider
from codebase_rag_mcp.generation.providers.groq import GroqProvider
from codebase_rag_mcp.generation.providers.local import LocalProvider
from codebase_rag_mcp.generation.providers.nvidia import NvidiaProvider
from codebase_rag_mcp.generation.providers.openrouter import OpenRouterProvider


def select_providers() -> list[Provider]:
    """Return configured providers in NVIDIA -> Groq -> OpenRouter -> Gemini -> Local order.

    A provider is included only if its credential (or, for Local,
    `config.LOCAL_MODEL_NAME`) is non-empty. Raises `NoProviderConfiguredError`
    if the resulting list would be empty -- there is nothing
    `generate_answer` could call.
    """
    keys = config.provider_keys()
    providers: list[Provider] = []

    if keys.nvidia:
        providers.append(NvidiaProvider())
    if keys.groq:
        providers.append(GroqProvider())
    if keys.openrouter:
        providers.append(OpenRouterProvider())
    if keys.gemini:
        providers.append(GeminiProvider())
    if config.LOCAL_MODEL_NAME:
        providers.append(LocalProvider())

    if not providers:
        raise NoProviderConfiguredError(
            "No LLM provider is configured -- set at least one of "
            "NVIDIA_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY, "
            "or LOCAL_MODEL_NAME."
        )
    return providers


__all__ = ["select_providers"]
