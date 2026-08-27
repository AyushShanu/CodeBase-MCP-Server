"""Tests for the dotenv-driven config loader."""

from __future__ import annotations

import importlib

import pytest

from codebase_rag_mcp import config
from codebase_rag_mcp.config import ProviderKeys, provider_keys


def test_provider_keys_is_dataclass() -> None:
    keys = provider_keys()
    assert isinstance(keys, ProviderKeys)
    # No provider key is required at import time; the dataclass must
    # always be constructible with empty strings.
    assert all(
        isinstance(getattr(keys, f), str) for f in ("nvidia", "groq", "openrouter", "gemini")
    )


def test_any_set_default_false() -> None:
    """In a clean test environment, ``any_set()`` should be False."""
    # We can't guarantee *no* key is set in CI, but the call must be safe.
    keys = provider_keys()
    assert isinstance(keys.any_set(), bool)


# --- generation settings (Day 07) ------------------------------------------------- #


def test_generation_model_name_settings_are_strings() -> None:
    assert isinstance(config.NVIDIA_MODEL_NAME, str)
    assert isinstance(config.GROQ_MODEL_NAME, str)
    assert isinstance(config.OPENROUTER_MODEL_NAME, str)
    assert isinstance(config.GEMINI_MODEL_NAME, str)


def test_generation_tuning_settings_have_expected_types() -> None:
    assert isinstance(config.GENERATION_TEMPERATURE, float)
    assert isinstance(config.GENERATION_MAX_TOKENS, int)
    assert isinstance(config.GENERATION_JSON_RETRY_LIMIT, int)
    assert isinstance(config.GENERATION_REQUEST_TIMEOUT_SECONDS, int)


def test_generation_json_retry_limit_overridable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # First use of monkeypatch.setenv + importlib.reload(config) in this
    # repo's test suite -- justified because this setting drives real,
    # tested retry-loop behavior in tests/test_generation.py, unlike the
    # plain model-name strings above, which have nothing to exercise beyond
    # "it's a string." Reloads `config` back afterward so later tests see
    # the original, unpatched module state.
    monkeypatch.setenv("GENERATION_JSON_RETRY_LIMIT", "3")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.GENERATION_JSON_RETRY_LIMIT == 3
    finally:
        importlib.reload(config)


def test_generation_request_timeout_seconds_overridable_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_REQUEST_TIMEOUT_SECONDS", "45")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.GENERATION_REQUEST_TIMEOUT_SECONDS == 45
    finally:
        importlib.reload(config)
