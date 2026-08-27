"""Typed exceptions for the generation stage (generation.pipeline.generate_answer).

Every error `pipeline.py`/`providers/*.py` can raise is a subclass of
`GenerationError` so callers can catch broadly (`except GenerationError`) or
narrowly (`except ProviderRequestError`) as needed.
"""

from __future__ import annotations


class GenerationError(Exception):
    """Base class for all generation-stage errors."""


class NoProviderConfiguredError(GenerationError):
    """Raised by `providers.registry.select_providers`/`pipeline.generate_answer`
    before any network call when no cloud provider API key is set
    (`config.provider_keys().any_set()` is False) and `config.LOCAL_MODEL_NAME`
    is also unset -- there is nothing to call. Distinct from
    `AllProvidersFailedError`, which means candidates existed but every one
    of them failed."""


class ProviderRequestError(GenerationError):
    """Raised by an individual `Provider.complete()` implementation on HTTP
    failure, timeout, or a malformed/unparseable response. Caught by
    `pipeline.generate_answer` to trigger fallback to the next configured
    provider in the chain -- never left to propagate out of `complete()`."""


class AllProvidersFailedError(GenerationError):
    """Raised by `pipeline.generate_answer` once every provider returned by
    `select_providers` has failed (a `ProviderRequestError` on every attempt,
    or an exhausted JSON-retry budget), wrapping each provider's last error
    for diagnosis."""


__all__ = [
    "AllProvidersFailedError",
    "GenerationError",
    "NoProviderConfiguredError",
    "ProviderRequestError",
]
