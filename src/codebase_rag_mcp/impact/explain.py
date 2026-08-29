"""Impact-explanation stage entry point: symbol + deterministic evidence
-> narrated explanation, reusing Day 07's provider fallback chain and
extract-JSON-then-Pydantic-validate pattern.

`_extract_json_object`/`_build_retry_prompt` are duplicated from
`generation.pipeline` rather than imported (they're private,
leading-underscore, module-internal there) -- matches this codebase's
existing convention of small helpers being independently duplicated per
stage rather than one stage reaching into another's private internals.

The anti-fabrication check runs *inside* the existing per-provider/
per-attempt retry loop, treated exactly like a JSON-shape validation
failure: if `_LLMImpactOutput.referenced_files` contains any file not in
the real evidence-file set, that attempt is failed, the model gets a
corrective retry prompt naming exactly which files were fabricated, and
if the retry budget is exhausted that provider is marked failed and the
next one is tried. `explain_impact` therefore never returns a narrative
built from a fabricated-files response -- it returns a fully-verified
narrative or raises `AllProvidersFailedError`/`NoProviderConfiguredError`
unwrapped, exactly like `generate_answer`; the caller
(`impact.analyzer.analyze_impact`) is responsible for catching those to
degrade `explanation` to `None` rather than failing the whole call.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from codebase_rag_mcp import config
from codebase_rag_mcp.generation.exceptions import AllProvidersFailedError, ProviderRequestError
from codebase_rag_mcp.generation.providers.registry import select_providers
from codebase_rag_mcp.impact.models import ImpactResult, _LLMImpactOutput
from codebase_rag_mcp.impact.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> str:
    """Best-effort isolation of the JSON object substring within `text`.

    Duplicated from `generation.pipeline._extract_json_object` -- same
    brace-matching-with-string-awareness logic, same "never raises"
    contract. See that function's docstring for the full rationale.
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
    return (
        f"{user_prompt}\n\n"
        "Your previous response was not valid JSON matching the required "
        f"schema. Validation error: {validation_error}\n"
        "Respond again with only the corrected JSON object -- no code "
        "fences, no prose before or after it."
    )


def _build_fabrication_retry_prompt(user_prompt: str, fabricated: list[str]) -> str:
    return (
        f"{user_prompt}\n\n"
        f'Your previous response\'s "referenced_files" named file(s) not '
        f"present in the evidence above: {fabricated}. Respond again, "
        "referencing only files that actually appear in the evidence."
    )


def _real_evidence_files(result: ImpactResult) -> set[str]:
    return (
        {d.file for d in result.definitions}
        | {c.file for c in result.callers}
        | {i.file for i in result.importers}
    )


def explain_impact(symbol: str, result: ImpactResult) -> str:
    """Narrate `result`'s deterministic evidence via the configured LLM
    provider fallback chain.

    Only ever called when `result.has_evidence` is `True`. Raises
    `NoProviderConfiguredError`/`AllProvidersFailedError` on failure --
    unwrapped, exactly like `generate_answer`; callers wanting graceful
    degradation (`impact.analyzer.analyze_impact`) catch these
    themselves. Never returns a narrative built from a response naming a
    fabricated file -- a fabrication is treated as a retry-worthy
    failure, same as a JSON-shape validation error.
    """
    real_files = _real_evidence_files(result)
    user_prompt = build_user_prompt(symbol, result)
    providers = select_providers()

    last_errors: dict[str, str] = {}
    for provider in providers:
        current_user_prompt = user_prompt
        narrative: str | None = None

        for attempt in range(config.GENERATION_JSON_RETRY_LIMIT + 1):
            try:
                raw_text = provider.complete(system=SYSTEM_PROMPT, user=current_user_prompt)
            except ProviderRequestError as exc:
                logger.warning("explain_impact: provider %s failed: %s", provider.name, exc)
                last_errors[provider.name] = str(exc)
                break

            extracted = _extract_json_object(raw_text)
            try:
                candidate = _LLMImpactOutput.model_validate_json(extracted)
            except ValidationError as exc:
                last_errors[provider.name] = str(exc)
                if attempt >= config.GENERATION_JSON_RETRY_LIMIT:
                    logger.warning(
                        "explain_impact: provider %s exhausted JSON retry budget: %s",
                        provider.name,
                        exc,
                    )
                    break
                current_user_prompt = _build_retry_prompt(user_prompt, str(exc))
                continue

            fabricated = [f for f in candidate.referenced_files if f not in real_files]
            if fabricated:
                logger.warning(
                    "explain_impact: provider %s fabricated files %s", provider.name, fabricated
                )
                last_errors[provider.name] = f"fabricated referenced_files: {fabricated}"
                if attempt >= config.GENERATION_JSON_RETRY_LIMIT:
                    break
                current_user_prompt = _build_fabrication_retry_prompt(user_prompt, fabricated)
                continue

            narrative = candidate.narrative
            break

        if narrative is not None:
            logger.info("explain_impact: provider=%s symbol=%r", provider.name, symbol)
            return narrative

    logger.warning("explain_impact: all %d configured providers failed", len(providers))
    raise AllProvidersFailedError(f"All configured providers failed: {last_errors}")


__all__ = ["explain_impact"]
