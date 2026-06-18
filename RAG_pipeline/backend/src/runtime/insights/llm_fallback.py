"""Bounded, opt-in LLM fallback for low-confidence runtime incidents.

This is a Decorator over the deterministic incident result produced by
``builder._build_incident``. It escalates to a single, bounded, credential-redacted
LLM call ONLY when:

  1. ``settings.runtime_llm_fallback_enabled`` is True (default False), AND
  2. the deterministic confidence is exactly ``"low"``, AND
  3. the LLM call succeeds and its output parses.

In every other case the deterministic root cause / next checks are returned
unchanged with ``cause_origin = "deterministic"``. The fallback never overwrites a
high/medium deterministic result and never raises into the build pipeline: any LLM
or parse failure falls back to the deterministic result (fail-safe).

Bounds (hard, enforced before the call): a single ``generate_answer`` call; at most
``_MAX_EXCERPTS`` evidence excerpts; each excerpt at most ``_MAX_EXCERPT_CHARS``
characters; the assembled prompt truncated to ``_MAX_PROMPT_CHARS`` (a char-based
proxy for an input-token cap). Only the bounded, redacted excerpts and structured
signals are sent — never raw log windows. Every excerpt and structured value is
re-run through ``redact_credentials`` as defense-in-depth before assembly.

Provider-neutral: reuses ``src/generation/llm_client.generate_answer`` (no new
provider, no Claude CLI dependency).
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ConfigDict

from src.config import settings
from src.runtime.insights.parser import redact_credentials

logger = logging.getLogger(__name__)

_MAX_EXCERPTS = 5
_MAX_EXCERPT_CHARS = 300
_MAX_PROMPT_CHARS = 6000

_DETERMINISTIC = "deterministic"
_LLM_FALLBACK = "llm_fallback"
_LOW = "low"

_SYSTEM_PROMPT = (
    "You are a runtime-failure triage assistant. You are given bounded, "
    "credential-redacted evidence from a single failed job execution whose "
    "automated root-cause confidence was low. Infer the most likely root cause and "
    "concrete next checks. Use ONLY the supplied evidence; do not invent specifics. "
    "Respond with a single JSON object and nothing else, of the form "
    '{"root_cause": "<one sentence>", "next_checks": ["<check>", "<check>"]}.'
)


class LlmFallbackEvidence(BaseModel):
    """One bounded, already-redacted evidence excerpt sent to the LLM."""

    model_config = ConfigDict(frozen=True)

    role: str
    excerpt: str


class LlmFallbackInput(BaseModel):
    """Bounded, redacted input assembled for the single LLM fallback call."""

    model_config = ConfigDict(frozen=True)

    symptom_summary: str
    error_code: str | None = None
    exception_class: str | None = None
    failed_component: str | None = None
    excerpts: list[LlmFallbackEvidence]


class LlmFallbackResult(BaseModel):
    """Parsed candidate root cause and next checks from the LLM fallback."""

    model_config = ConfigDict(frozen=True)

    root_cause_candidate: str
    next_checks: list[str]


def build_fallback_input(
    *,
    symptom_summary: str,
    error_code: str | None,
    exception_class: str | None,
    failed_component: str | None,
    excerpts: list[tuple[str, str]],
) -> LlmFallbackInput:
    """Assemble the bounded, redacted fallback input from (role, excerpt) pairs.

    Enforces the count and per-excerpt char caps and re-applies credential
    redaction to every string as defense-in-depth, so nothing unredacted can reach
    the LLM even if a caller passes raw text.
    """
    bounded: list[LlmFallbackEvidence] = []
    for role, text in excerpts[:_MAX_EXCERPTS]:
        redacted = redact_credentials(text)[:_MAX_EXCERPT_CHARS]
        bounded.append(LlmFallbackEvidence(role=role, excerpt=redacted))
    return LlmFallbackInput(
        symptom_summary=redact_credentials(symptom_summary)[:_MAX_EXCERPT_CHARS],
        error_code=redact_credentials(error_code) if error_code else None,
        exception_class=redact_credentials(exception_class) if exception_class else None,
        failed_component=redact_credentials(failed_component) if failed_component else None,
        excerpts=bounded,
    )


def _render_user_prompt(payload: LlmFallbackInput) -> str:
    lines: list[str] = ["Failed execution evidence:", f"symptom: {payload.symptom_summary}"]
    if payload.error_code:
        lines.append(f"error_code: {payload.error_code}")
    if payload.exception_class:
        lines.append(f"exception_class: {payload.exception_class}")
    if payload.failed_component:
        lines.append(f"failed_component: {payload.failed_component}")
    for ev in payload.excerpts:
        lines.append(f"- [{ev.role}] {ev.excerpt}")
    prompt = "\n".join(lines)
    return prompt[:_MAX_PROMPT_CHARS]


def _parse_llm_output(raw: str) -> LlmFallbackResult | None:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    root_cause = data.get("root_cause")
    next_checks = data.get("next_checks")
    if not isinstance(root_cause, str) or not root_cause.strip():
        return None
    if not isinstance(next_checks, list):
        return None
    checks = [str(c).strip() for c in next_checks if str(c).strip()]
    if not checks:
        return None
    return LlmFallbackResult(
        root_cause_candidate=redact_credentials(root_cause.strip()),
        next_checks=[redact_credentials(c) for c in checks],
    )


async def maybe_run_llm_fallback(
    deterministic_root_cause: str,
    deterministic_next_checks: list[str],
    *,
    confidence: str,
    symptom_summary: str,
    error_code: str | None,
    exception_class: str | None,
    failed_component: str | None,
    excerpts: list[tuple[str, str]],
) -> tuple[str, list[str], str]:
    """Return (root_cause, next_checks, cause_origin) for an incident.

    Returns the deterministic pair + ``"deterministic"`` unchanged unless the flag
    is on, ``confidence == "low"``, and a single bounded LLM call succeeds and
    parses — in which case the LLM candidate + ``"llm_fallback"`` is returned.
    Fail-safe: any LLM/parse error keeps the deterministic result; never raises.
    """
    if not settings.runtime_llm_fallback_enabled:
        return deterministic_root_cause, deterministic_next_checks, _DETERMINISTIC
    if confidence != _LOW:
        return deterministic_root_cause, deterministic_next_checks, _DETERMINISTIC

    payload = build_fallback_input(
        symptom_summary=symptom_summary,
        error_code=error_code,
        exception_class=exception_class,
        failed_component=failed_component,
        excerpts=excerpts,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _render_user_prompt(payload)},
    ]

    from src.generation.llm_client import generate_answer

    try:
        raw = await generate_answer(messages)
    except Exception as e:  # noqa: BLE001 - fail-safe boundary: never crash the build pipeline
        # The LLM fallback is a best-effort escalation. Any failure (provider chain
        # exhausted, network, timeout, unexpected client error) must keep the
        # deterministic result rather than aborting insight construction.
        logger.warning(
            "Runtime LLM fallback call failed; keeping deterministic result: [%s] %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return deterministic_root_cause, deterministic_next_checks, _DETERMINISTIC

    result = _parse_llm_output(raw)
    if result is None:
        logger.warning(
            "Runtime LLM fallback returned unparseable output; keeping deterministic result"
        )
        return deterministic_root_cause, deterministic_next_checks, _DETERMINISTIC

    return result.root_cause_candidate, result.next_checks, _LLM_FALLBACK
