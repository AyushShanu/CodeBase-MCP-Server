"""Gemini adapter -- a separate implementation, not `_openai_compatible.py`.

Gemini's `generateContent` REST endpoint has a different envelope from the
OpenAI-compatible four (`contents`/`systemInstruction`/`generationConfig`
instead of `messages`, and a differently-shaped response), and -- unlike the
other four, which authenticate via an `Authorization` header -- authenticates
via a `key=` query parameter. That means the "never log secrets" discipline
`base.py` states for headers must extend here to the request URL itself:
this adapter must never log the full request URL verbatim on failure, only
the model name and HTTP status.
"""

from __future__ import annotations

import logging

import httpx

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.exceptions import ProviderRequestError

logger = logging.getLogger(__name__)


class GeminiProvider:
    """`Provider` for Google's Gemini `generateContent` REST endpoint."""

    name = "gemini"

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def complete(self, *, system: str, user: str) -> str:
        """Send `system`/`user` to Gemini's `generateContent` endpoint.

        Requests native JSON output via `generationConfig.responseMimeType:
        "application/json"` as defense-in-depth alongside `pipeline.py`'s own
        `_extract_json_object` step. An explicit
        `timeout=config.GENERATION_REQUEST_TIMEOUT_SECONDS` is always passed;
        any request/parse failure (including a timeout) is wrapped into
        `ProviderRequestError`, never left to escape as a bare `httpx`
        exception, and the API key embedded in the request URL is never
        logged.
        """
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.GEMINI_MODEL_NAME}:generateContent"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": config.GENERATION_TEMPERATURE,
                "maxOutputTokens": config.GENERATION_MAX_TOKENS,
            },
        }

        try:
            with httpx.Client(
                transport=self._transport,
                timeout=config.GENERATION_REQUEST_TIMEOUT_SECONDS,
            ) as client:
                response = client.post(url, params={"key": config.GEMINI_API_KEY}, json=body)
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
            return str(payload["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("%s returned a malformed response body", self.name)
            raise ProviderRequestError(f"{self.name} returned a malformed response body") from exc


__all__ = ["GeminiProvider"]
