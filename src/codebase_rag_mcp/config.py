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


# --- Runtime configuration ------------------------------------------------- #

LOG_LEVEL: Final[str] = _getenv("LOG_LEVEL", "INFO")
DATA_DIR: Final[str] = _getenv("DATA_DIR", "./data")
INDEX_DIR: Final[str] = _getenv("INDEX_DIR", "./data/index")


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
    "DATA_DIR",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_MODEL_NAME",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "HYBRID_CANDIDATE_POOL_SIZE",
    "INDEX_DIR",
    "LOCAL_MODEL_API_KEY",
    "LOCAL_MODEL_BASE_URL",
    "LOCAL_MODEL_NAME",
    "LOG_LEVEL",
    "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY",
    "RRF_K",
    "ProviderKeys",
    "provider_keys",
]
