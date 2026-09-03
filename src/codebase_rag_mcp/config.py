"""Configuration loading via ``python-dotenv``.

Loads ``.env`` (if present) and exposes typed accessors for the provider
keys and local-model settings listed in ``.env.example``. Missing values
are returned as empty strings rather than raising, so import-time code
that just inspects configuration does not blow up before the operator
has had a chance to populate ``.env``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import platformdirs
from dotenv import load_dotenv

from codebase_rag_mcp.mcp.exceptions import InvalidIndexDirError

# Duplicated from `ingestion.loader.ALLOWED_URL_SCHEMES` rather than
# imported -- `ingestion.loader` itself imports this module (for
# `DATA_DIR`), so importing it back here would create a cycle.
_ALLOWED_URL_SCHEMES = frozenset({"https"})


def _resolve_effective_source(
    *, repo_flag: str | None, repo_source_env: str, cwd: str | Path | None = None
) -> str | None:
    """Resolve the repo source zero-config auto-indexing should target, or
    `None` if none can be safely inferred (Day 12).

    Precedence: an explicit `--repo` flag, then a non-empty `REPO_SOURCE`
    env var, then `cwd` itself -- but ONLY if `cwd` contains a `.git`
    directory. The `.git` gate applies to this bare-`cwd` fallback tier
    alone: an explicit `--repo`/`REPO_SOURCE` value (a local path or an
    `https://` URL) is a deliberate user instruction and is honored as-is,
    `.git` or not. Without the gate, launching `serve` from an arbitrary
    directory (a home folder, a Downloads folder) with no explicit source
    configured would silently scan and embed whatever happened to be
    there.

    `cwd` is a test seam -- production callers leave it `None`, resolving
    the real `Path.cwd()`.

    Relocated here from `mcp/server.py` in Day 13
    (13-cross-agent-mcp-packaging-portability) -- `_resolve_env_path`
    (below) needs this function's output *before* `mcp.server` can safely
    be imported (see that function's own docstring), and this function has
    no heavy dependencies of its own, so `config.py` -- already imported
    early and cheaply everywhere -- is its natural home. Re-exported
    unchanged from `mcp.server` for backward compatibility.
    """
    if repo_flag:
        return repo_flag
    if repo_source_env:
        return repo_source_env
    real_cwd = Path(cwd) if cwd is not None else Path.cwd()
    if (real_cwd / ".git").exists():
        return str(real_cwd)
    return None


def _canonicalize_repo_source(repo_source: str) -> str:
    """Normalize `repo_source` to a stable string for hashing (Day 13).

    A local path is resolved to its absolute, symlink-resolved form
    (non-strict -- the path may not exist yet, e.g. a URL source that
    hasn't been cloned for the first time). A remote URL is normalized by
    lowercasing its scheme and host and stripping a trailing `.git` and/or
    trailing `/`, so trivially-different spellings of the same remote
    (trailing slash, `.git` suffix, host casing) hash identically.
    """
    scheme = urlparse(repo_source).scheme.lower()
    if scheme in _ALLOWED_URL_SCHEMES:
        parsed = urlparse(repo_source)
        path = parsed.path
        if path.endswith("/"):
            path = path[:-1]
        if path.endswith(".git"):
            path = path[: -len(".git")]
        return f"{scheme}://{parsed.netloc.lower()}{path}"
    return str(Path(repo_source).resolve())


def _resolve_index_dir(repo_source: str | None, explicit: str | Path | None) -> Path:
    """Resolve the directory a `serve`/`index` invocation should read/write
    its vector/BM25/reference index and manifest under (Day 13).

    An explicit `--index-dir`/`INDEX_DIR` value always overrides everything
    below and is honored verbatim -- but it must be absolute. A relative
    value is **rejected outright** (`InvalidIndexDirError`), never silently
    resolved against `cwd`: `cwd` is exactly the launch-directory-dependent
    property this day exists to stop mattering, so silently reinterpreting
    a relative `--index-dir` against it would reintroduce the same bug
    class this function is meant to close.

    With no explicit override and a resolvable `repo_source`, the index
    directory is keyed by a short hash of that source's canonicalized form
    under an OS-appropriate user-data directory (`platformdirs`):
    `<user_data_dir>/index/<16-hex sha256 of canonical source>/`. This is
    what makes the same repo always resolve to the same index directory
    regardless of which directory or MCP client launched the server, while
    two different repos can never collide on one shared default.

    With no explicit override and no resolvable `repo_source` (`None` or
    `""` -- e.g. `serve` was launched with no `--repo`/`REPO_SOURCE` and
    `cwd` has no `.git`), this degrades to the static `INDEX_DIR` default
    below. This is not a new collision risk: without a resolved source, no
    index build could ever have been started in the first place (see
    `IndexNotAvailableError`), so there is nothing to collide with.

    Never creates the directory -- each `indexing/*` writer already does
    `Path(index_dir).mkdir(parents=True, exist_ok=True)` itself.
    """
    if explicit is not None:
        explicit_path = Path(explicit)
        if not explicit_path.is_absolute():
            raise InvalidIndexDirError(
                f"--index-dir/INDEX_DIR must be an absolute path, got {str(explicit)!r} -- "
                "a relative value is rejected outright rather than silently resolved "
                "against the current directory, since the launch directory is exactly "
                "what this project's cross-agent portability work (day "
                "13-cross-agent-mcp-packaging-portability) makes irrelevant."
            )
        return explicit_path
    if not repo_source:
        return Path(INDEX_DIR)
    canonical = _canonicalize_repo_source(repo_source)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return Path(platformdirs.user_data_dir("codebase-rag")) / "index" / digest


def _resolve_env_path(repo_source: str, explicit: str | Path | None) -> Path | None:
    """Resolve which single `.env` file (if any) should be loaded (Day 13).

    Precedence, first candidate that exists wins (an explicit `--env-file`
    is the one exception -- it is returned even if missing, so a typo'd
    path is debuggable via the startup log rather than silently swapped
    for a different file):

    1. An explicit `--env-file` path, if given.
    2. `<repo_source>/.env`, if `repo_source` is a *local* path (not a
       remote URL) and that file exists.
    3. `<os.getcwd()>/.env`, if it exists -- today's original behavior,
       kept as the final fallback so a developer running `codebase-rag`
       from inside their own cloned checkout is unaffected.
    4. `None` -- no `.env` file is loaded at all. This is the expected,
       recommended case for a packaged/installed server: provider keys
       come from the MCP client's own server-registration `"env"` block,
       a real process environment variable, which `load_dotenv(...,
       override=False)` never overrides regardless of this function's
       result.

    Exactly one file is selected and loaded, never several layered
    together -- layering would be unable to correctly let a
    higher-precedence file override a lower-precedence one for the same
    variable, since `override=False` cannot distinguish "already set by a
    real env var" from "already set by an earlier, lower-precedence `.env`
    load" once both are sitting in `os.environ`.
    """
    if explicit is not None:
        return Path(explicit)
    if repo_source and urlparse(repo_source).scheme.lower() not in _ALLOWED_URL_SCHEMES:
        repo_env = Path(repo_source) / ".env"
        if repo_env.is_file():
            return repo_env
    cwd_env = Path(os.getcwd()) / ".env"
    if cwd_env.is_file():
        return cwd_env
    return None


# Load .env once at import time, from whichever single path
# `_resolve_env_path` selects for a bare `import config` (no repo source or
# explicit override known yet -- this degrades to tier 3 above, i.e.
# today's original cwd-relative behavior, for every existing caller).
# `override=False` means a real environment variable (set by CI, a shell
# export, or an MCP client's own launch-config "env" block) always takes
# precedence over any `.env` file's value -- see DECISIONS.md D-027.
_DOTENV_PATH: Path | None = _resolve_env_path("", explicit=None)
if _DOTENV_PATH is not None:
    load_dotenv(_DOTENV_PATH, override=False)


def _getenv(name: str, default: str = "") -> str:
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
