"""Shared adapter for the four OpenAI-chat-completions-shaped backends.

NVIDIA NIM, Groq, OpenRouter, and any local OpenAI-compatible server (Ollama,
vLLM, LM Studio, llama.cpp server) all speak the same `/chat/completions`
REST shape, differing only in `base_url`/`api_key`/`model_name`. Sharing one
implementation here is a deliberate exception to "don't build abstractions
before they're needed" -- four call sites with an identical request/response
shape is exactly the "three similar lines" case CLAUDE.md's own conventions
say to collapse, not the premature-abstraction case they warn against.
Gemini's REST shape differs enough (auth in the URL, a different envelope)
that it gets its own separate adapter (`gemini.py`) instead.
"""

from __future__ import annotations

import logging

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.exceptions import ProviderRequestError

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """A `Provider` backed by an OpenAI-compatible `/chat/completions` endpoint.

    Accepts an optional `transport` (an `httpx.BaseTransport`, e.g.
    `httpx.MockTransport`) purely as a test seam -- production code never
    passes one, so a fresh, real `httpx.Client` is built per call. This
    keeps `complete`'s signature exactly what `Provider` specifies; the seam
    lives at construction time instead.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model_name: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url
        self._api_key = api_key
        self._model_name = model_name
        self._transport = transport

    def complete(self, *, system: str, user: str) -> str:
        """Send `system`/`user` as a chat completion and return the raw reply text.

        Requests native JSON output via `response_format: {"type":
        "json_object"}` as a robustness win on top of `pipeline.py`'s own
        `_extract_json_object` extraction step -- not every model honors it,
        so this is defense-in-depth, not a substitute. Every request passes
        an explicit `timeout=config.GENERATION_REQUEST_TIMEOUT_SECONDS`; a
        timeout or any other request/parse failure is wrapped into
        `ProviderRequestError`, never left to escape as a bare `httpx`
        exception.
        """
        body = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": config.GENERATION_TEMPERATURE,
            "max_tokens": config.GENERATION_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(
                transport=self._transport,
                timeout=config.GENERATION_REQUEST_TIMEOUT_SECONDS,
            ) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            logger.warning(
                "%s request timed out after %ss",
                self.name,
                config.GENERATION_REQUEST_TIMEOUT_SECONDS,
            )
            raise ProviderRequestError(f"{self.name} request timed out") from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("%s request failed with status %s", self.name, exc.response.status_code)
            raise ProviderRequestError(
                f"{self.name} request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("%s request failed: %s", self.name, type(exc).__name__)
            raise ProviderRequestError(f"{self.name} request failed: {type(exc).__name__}") from exc

        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("%s returned a malformed response body", self.name)
            raise ProviderRequestError(f"{self.name} returned a malformed response body") from exc


__all__ = ["OpenAICompatibleProvider"]
