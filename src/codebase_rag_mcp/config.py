"""Configuration loading via ``python-dotenv``.

Loads ``.env`` (if present) and exposes typed accessors for the provider
keys and local-model settings listed in ``.env.example``. Missing values
are returned as empty strings rather than raising, so import-time code
that just inspects configuration does not blow up before the operator
has had a chance to populate ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from dotenv import load_dotenv

# Load .env once at import time. ``override=False`` means real environment
# variables (e.g. set by CI or the shell) take precedence over the file.
load_dotenv(override=False)


def _getenv(name: str, default: str = "") -> str:
    import os

    value = os.getenv(name)
    return value if value is not None else default


def _getenv_int(name: str, default: int) -> int:
    value = _getenv(name, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def _getenv_float(name: str, default: float) -> float:
    value = _getenv(name, str(default))
    try:
        return float(value)
    except ValueError:
        return default


def _getenv_bool(name: str, default: bool) -> bool:
    value = _getenv(name, str(default)).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


# --- Provider keys --------------------------------------------------------- #

NVIDIA_API_KEY: Final[str] = _getenv("NVIDIA_API_KEY")
GROQ_API_KEY: Final[str] = _getenv("GROQ_API_KEY")
OPENROUTER_API_KEY: Final[str] = _getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY: Final[str] = _getenv("GEMINI_API_KEY")


# --- Local-model settings -------------------------------------------------- #

LOCAL_MODEL_BASE_URL: Final[str] = _getenv("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
LOCAL_MODEL_NAME: Final[str] = _getenv("LOCAL_MODEL_NAME")
LOCAL_MODEL_API_KEY: Final[str] = _getenv("LOCAL_MODEL_API_KEY")


# --- Embedding settings ----------------------------------------------------- #

EMBEDDING_MODEL_NAME: Final[str] = _getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDING_BATCH_SIZE: Final[int] = _getenv_int("EMBEDDING_BATCH_SIZE", 32)


# --- Retrieval settings ----------------------------------------------------- #

RRF_K: Final[int] = _getenv_int("RRF_K", 60)
HYBRID_CANDIDATE_POOL_SIZE: Final[int] = _getenv_int("HYBRID_CANDIDATE_POOL_SIZE", 50)


# --- Reranker settings ------------------------------------------------------ #

RERANKER_MODEL_NAME: Final[str] = _getenv(
    "RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
RERANKER_MAX_LENGTH: Final[int] = _getenv_int("RERANKER_MAX_LENGTH", 512)
RERANK_TOP_N: Final[int] = _getenv_int("RERANK_TOP_N", 8)


# --- Generation settings ---------------------------------------------------- #
#
# Per-provider default model, confirmed against each provider's genuinely
# free tier as of 2026-08 (see DECISIONS.md D-022 for the confirmation
# sources) -- catalogs drift, so these are overridable via env and are not
# meant to be permanent. Note `meta/llama-3.1-8b-instruct` (NVIDIA's older
# small default) was deprecated 2026-08-25 and is deliberately not used here.

NVIDIA_MODEL_NAME: Final[str] = _getenv(
    "NVIDIA_MODEL_NAME", "nvidia/llama-3.3-nemotron-super-49b-v1"
)
GROQ_MODEL_NAME: Final[str] = _getenv("GROQ_MODEL_NAME", "llama-3.1-8b-instant")
OPENROUTER_MODEL_NAME: Final[str] = _getenv(
    "OPENROUTER_MODEL_NAME", "meta-llama/llama-3.3-70b-instruct:free"
)
GEMINI_MODEL_NAME: Final[str] = _getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")

GENERATION_TEMPERATURE: Final[float] = _getenv_float("GENERATION_TEMPERATURE", 0.1)
GENERATION_MAX_TOKENS: Final[int] = _getenv_int("GENERATION_MAX_TOKENS", 1024)
GENERATION_JSON_RETRY_LIMIT: Final[int] = _getenv_int("GENERATION_JSON_RETRY_LIMIT", 1)
GENERATION_REQUEST_TIMEOUT_SECONDS: Final[int] = _getenv_int(
    "GENERATION_REQUEST_TIMEOUT_SECONDS", 30
)


# --- Runtime configuration ------------------------------------------------- #

LOG_LEVEL: Final[str] = _getenv("LOG_LEVEL", "INFO")
DATA_DIR: Final[str] = _getenv("DATA_DIR", "./data")
INDEX_DIR: Final[str] = _getenv("INDEX_DIR", "./data/index")


# --- Auto-indexing settings (Day 12: zero-config `serve` startup) ---------- #

REPO_SOURCE: Final[str] = _getenv("REPO_SOURCE")
AUTO_INDEX: Final[bool] = _getenv_bool("AUTO_INDEX", True)


@dataclass(frozen=True, slots=True)
class ProviderKeys:
    """Snapshot of cloud LLM provider keys loaded from the environment."""

    nvidia: str = NVIDIA_API_KEY
    groq: str = GROQ_API_KEY
    openrouter: str = OPENROUTER_API_KEY
    gemini: str = GEMINI_API_KEY

    def any_set(self) -> bool:
        """Return True if at least one provider key is configured."""
        return any((self.nvidia, self.groq, self.openrouter, self.gemini))


def provider_keys() -> ProviderKeys:
    """Return a snapshot of provider keys loaded from the environment."""
    return ProviderKeys()


__all__ = [
    "AUTO_INDEX",
    "DATA_DIR",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_MODEL_NAME",
    "GEMINI_API_KEY",
    "GEMINI_MODEL_NAME",
    "GENERATION_JSON_RETRY_LIMIT",
    "GENERATION_MAX_TOKENS",
    "GENERATION_REQUEST_TIMEOUT_SECONDS",
    "GENERATION_TEMPERATURE",
    "GROQ_API_KEY",
    "GROQ_MODEL_NAME",
    "HYBRID_CANDIDATE_POOL_SIZE",
    "INDEX_DIR",
    "LOCAL_MODEL_API_KEY",
    "LOCAL_MODEL_BASE_URL",
    "LOCAL_MODEL_NAME",
    "LOG_LEVEL",
    "NVIDIA_API_KEY",
    "NVIDIA_MODEL_NAME",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL_NAME",
    "REPO_SOURCE",
    "RERANKER_MAX_LENGTH",
    "RERANKER_MODEL_NAME",
    "RERANK_TOP_N",
    "RRF_K",
    "ProviderKeys",
    "provider_keys",
]
