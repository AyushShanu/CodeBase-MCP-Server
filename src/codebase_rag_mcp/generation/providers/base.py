"""Common interface every LLM provider adapter implements.

`Provider` is a `typing.Protocol` rather than an `abc.ABC` -- there is no
ABC/interface precedent anywhere else in this codebase, and structural
typing lets test fakes satisfy the interface without needing to import or
subclass anything, matching this project's "don't build abstractions before
they're needed" ethos (see DECISIONS.md D-022).

Every concrete adapter reads its own API key/base URL/model name from
`config` at call time -- no persistent client caching, the same "Day 08 owns
lifecycle" convention `reranker.rerank`/`retrieval.hybrid` already established.

**Never log a request's headers or body verbatim on failure.** The
`Authorization` header (or, for `gemini.py`, the `key=` query parameter)
carries the provider's API key -- any failure logging must be limited to the
provider name, HTTP status code, and a truncated/sanitized error message.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """A single LLM backend `generation.pipeline.generate_answer` can call.

    `complete` sends `system`/`user` messages to the backend and returns its
    raw response text, unparsed -- extracting/validating structured JSON out
    of that text is `pipeline.py`'s job, not any adapter's. Raises
    `generation.exceptions.ProviderRequestError` on any failure, including a
    request timeout; never raises a bare `httpx` exception.
    """

    name: str

    def complete(self, *, system: str, user: str) -> str: ...


__all__ = ["Provider"]
