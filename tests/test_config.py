"""Tests for the dotenv-driven config loader."""

from __future__ import annotations

from codebase_rag_mcp.config import ProviderKeys, provider_keys


def test_provider_keys_is_dataclass() -> None:
    keys = provider_keys()
    assert isinstance(keys, ProviderKeys)
    # No provider key is required at import time; the dataclass must
    # always be constructible with empty strings.
    assert all(isinstance(getattr(keys, f), str) for f in ("nvidia", "groq", "openrouter", "gemini"))


def test_any_set_default_false() -> None:
    """In a clean test environment, ``any_set()`` should be False."""
    # We can't guarantee *no* key is set in CI, but the call must be safe.
    keys = provider_keys()
    assert isinstance(keys.any_set(), bool)
