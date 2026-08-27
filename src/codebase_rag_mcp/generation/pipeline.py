"""Generation stage entry point: query + reranked evidence -> cited answer.

`generate_answer` is the last non-MCP pipeline stage (Day 08's MCP tools call
into this rather than adding new generation logic of their own). It never
calls a provider when there is no evidence to answer from (`candidates ==
[]`), builds a strict evidence-only prompt (`generation.prompts`), and
iterates `providers.registry.select_providers()`'s configured chain with
runtime fallback-on-failure -- config-time precedence (which candidates
exist) and runtime fallback (which candidate's failure moves to the next)
are two distinct mechanisms, see DECISIONS.md D-022.

A provider's raw response text is never trusted directly: it is run through
`_extract_json_object` (defensive against code-fence-wrapped or prose-
prefixed JSON, a common failure mode for free-tier instruct models) and then
validated against `generation.models._LLMStructuredOutput` before anything
downstream sees it. Citation attachment (`citations.attach.attach_citations`)
is the actual "no fabricated citations" enforcement -- the model only ever
supplies which chunk IDs it used; this project's own indexed metadata
supplies everything else. A citation list that comes back empty forces
`has_sufficient_evidence=False` regardless of what the model claimed.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from codebase_rag_mcp import config
from codebase_rag_mcp.citations.attach import attach_citations
from codebase_rag_mcp.generation.exceptions import AllProvidersFailedError, ProviderRequestError
from codebase_rag_mcp.generation.models import GeneratedAnswer, _LLMStructuredOutput
from codebase_rag_mcp.generation.prompts import SYSTEM_PROMPT, build_user_prompt
from codebase_rag_mcp.generation.providers.registry import select_providers
from codebase_rag_mcp.reranker.models import RerankedResult

logger = logging.getLogger(__name__)

_NO_EVIDENCE_ANSWER = "I don't have enough retrieved evidence to answer this question."


def _extract_json_object(text: str) -> str:
    """Best-effort isolation of the JSON object substring within `text`.

    Real free-tier instruct models routinely wrap valid JSON in ` ```json
    ... ``` ` fences or add a sentence of prose despite instructions not to.
    Strips a leading/trailing code fence if present, then brace-matches
    forward from the first `{` -- tracking whether the scan is inside a JSON
    string literal (respecting `\\"` escapes) so a literal brace inside an
    `"answer"` string value doesn't break the match -- and returns the
    balanced outermost object. Never raises: if no balanced object is found,
    returns the fence-stripped text unchanged, letting the caller's schema
    validation fail naturally rather than the extractor itself erroring.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        newline_index = stripped.find("\n")
        stripped = stripped[newline_index + 1 :] if newline_index != -1 else ""
        stripped = stripped.strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()

    start = stripped.find("{")
    if start == -1:
        return stripped

    depth = 0
    in_string = False
    escape_next = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    return stripped


def _build_retry_prompt(user_prompt: str, validation_error: str) -> str:
    """Append a validation-error-repair instruction to `user_prompt`.

    Used to re-prompt the *same* provider within its JSON-retry budget --
    never sent to a different provider.
    """
    return (
        f"{user_prompt}\n\n"
        "Your previous response was not valid JSON matching the required "
        f"schema. Validation error: {validation_error}\n"
        "Respond again with only the corrected JSON object -- no code "
        "fences, no prose before or after it."
    )


def generate_answer(query: str, candidates: list[RerankedResult]) -> GeneratedAnswer:
    """Produce a citation-backed answer to `query` from `candidates`.

    If `candidates` is empty, returns a canned insufficient-evidence
    `GeneratedAnswer` (`provider_used=None`) without calling
    `select_providers` or any provider -- zero evidence means zero tokens
    spent. Otherwise iterates the configured provider chain in order; each
    provider gets up to `config.GENERATION_JSON_RETRY_LIMIT + 1` attempts
    (the first attempt plus that many JSON-repair retries) before its
    failure (a `ProviderRequestError`, including a timeout, or an exhausted
    JSON-retry budget) moves on to the next provider. Raises
    `AllProvidersFailedError` only once every configured provider has
    failed; `NoProviderConfiguredError` (from `select_providers`) propagates
    unwrapped and is distinct from that -- it means nothing was ever a
    candidate, not that candidates existed and failed.
    """
    if not candidates:
        return GeneratedAnswer(
            answer=_NO_EVIDENCE_ANSWER,
            citations=[],
            has_sufficient_evidence=False,
            provider_used=None,
        )

    user_prompt = build_user_prompt(query, candidates)
    providers = select_providers()

    last_errors: dict[str, str] = {}
    for provider in providers:
        current_user_prompt = user_prompt
        structured: _LLMStructuredOutput | None = None

        for attempt in range(config.GENERATION_JSON_RETRY_LIMIT + 1):
            try:
                raw_text = provider.complete(system=SYSTEM_PROMPT, user=current_user_prompt)
            except ProviderRequestError as exc:
                logger.warning("generate_answer: provider %s failed: %s", provider.name, exc)
                last_errors[provider.name] = str(exc)
                break

            extracted = _extract_json_object(raw_text)
            try:
                structured = _LLMStructuredOutput.model_validate_json(extracted)
                break
            except ValidationError as exc:
                last_errors[provider.name] = str(exc)
                if attempt >= config.GENERATION_JSON_RETRY_LIMIT:
                    logger.warning(
                        "generate_answer: provider %s exhausted JSON retry budget: %s",
                        provider.name,
                        exc,
                    )
                    break
                current_user_prompt = _build_retry_prompt(user_prompt, str(exc))

        if structured is not None:
            citations = attach_citations(structured.cited_chunk_ids, candidates)
            has_sufficient_evidence = structured.has_sufficient_evidence and bool(citations)
            logger.info(
                "generate_answer: provider=%s has_sufficient_evidence=%s citations=%d",
                provider.name,
                has_sufficient_evidence,
                len(citations),
            )
            return GeneratedAnswer(
                answer=structured.answer,
                citations=citations,
                has_sufficient_evidence=has_sufficient_evidence,
                provider_used=provider.name,
            )

    logger.warning("generate_answer: all %d configured providers failed", len(providers))
    raise AllProvidersFailedError(f"All configured providers failed: {last_errors}")


__all__ = ["generate_answer"]
