"""RAG chat LLM facade.

Public entry points (`generate_answer`, `generate_answer_stream`,
`generate_answer_with_runtime_tools`, `get_last_usage`, `cleanup_llm_clients`)
keep their historical signatures so the ~40 call sites are untouched. Routing now
goes through the provider registry: the superadmin-selected ``provider/model`` id
is resolved to exactly one provider, called with the bare model name, and its
errors are surfaced — there is no cross-provider fallback chain.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextvars import ContextVar

from src.generation.providers import registry
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition
from src.services.instance_settings_resolver import get_rag_chat_model_id

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility — callers import these from here.
__all__ = [
    "RuntimeToolCallRequest",
    "ToolAwareLLMResponse",
    "generate_answer",
    "generate_answer_stream",
    "generate_answer_with_runtime_tools",
    "generate_document_answer",
    "generate_document_answer_stream",
    "get_documentation_provider_trace",
    "get_last_usage",
    "get_turn_usage",
    "reset_turn_usage",
    "cleanup_llm_clients",
]

# Usage tracking lives in ContextVars so it is scoped to the current
# request/task: a turn that never calls the LLM reads None instead of stale
# stats from an unrelated request, and concurrent requests cannot overwrite
# each other's numbers. `_last_usage` holds the most recent call;
# `_turn_usage` accumulates every call made since `reset_turn_usage()`.
_last_usage: ContextVar[UsageStats | None] = ContextVar("llm_last_usage", default=None)
_turn_usage: ContextVar[UsageStats | None] = ContextVar("llm_turn_usage", default=None)
_documentation_provider_trace: ContextVar[dict | None] = ContextVar(
    "documentation_provider_trace", default=None
)


def get_documentation_provider_trace() -> dict | None:
    trace = _documentation_provider_trace.get()
    if trace is None:
        return None
    return {
        "provider_used": trace.get("provider_used"),
        "fallback_used": bool(trace.get("fallback_used")),
        "attempts": [dict(item) for item in trace.get("attempts", [])],
    }


def get_last_usage() -> UsageStats | None:
    """Return usage stats from the last completed generate call in this context."""
    return _last_usage.get()


def get_turn_usage() -> UsageStats | None:
    """Return a copy of the usage accumulated across all generate calls made
    in this context since the last `reset_turn_usage()` (or task start)."""
    total = _turn_usage.get()
    if total is None:
        return None
    return UsageStats(
        provider=total.provider,
        model=total.model,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
        cost_usd=total.cost_usd,
        latency_ms=total.latency_ms,
        cached_input_tokens=total.cached_input_tokens,
        cache_write_tokens=total.cache_write_tokens,
    )


def reset_turn_usage() -> None:
    """Clear last/accumulated usage. Call once at the start of each user turn,
    before any LLM call that should be attributed to that turn."""
    _last_usage.set(None)
    _turn_usage.set(None)


def _record_usage(usage: UsageStats) -> None:
    """Store `usage` as the last call and add it to the turn accumulator."""
    _last_usage.set(usage)
    total = _turn_usage.get()
    if total is None:
        total = UsageStats()
        _turn_usage.set(total)
    total.provider = usage.provider
    total.model = usage.model
    total.input_tokens += usage.input_tokens
    total.output_tokens += usage.output_tokens
    total.cost_usd += usage.cost_usd
    total.latency_ms += usage.latency_ms
    total.cached_input_tokens += usage.cached_input_tokens
    total.cache_write_tokens += usage.cache_write_tokens


async def _resolve_selection() -> tuple:
    """Resolve the active selection to (provider, bare_model_name)."""
    model_id = await get_rag_chat_model_id()
    provider, bare_model = registry.resolve(model_id)
    return provider, bare_model


async def _documentation_chain() -> list[tuple]:
    """Resolve the selected model plus explicitly approved fallback models."""

    from src.config import settings

    selected = await get_rag_chat_model_id()
    model_ids = [selected, *settings.rag_approved_fallback_models]
    resolved: list[tuple] = []
    seen: set[str] = set()
    for model_id in model_ids:
        normalized = str(model_id).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(registry.resolve(normalized))
    if not resolved:
        raise RuntimeError("No approved documentation generation provider is configured.")
    return resolved


def _set_documentation_trace(attempts: list[dict], provider_used: str | None) -> None:
    _documentation_provider_trace.set(
        {
            "provider_used": provider_used,
            "fallback_used": bool(provider_used and attempts and provider_used != attempts[0]["provider"]),
            "attempts": [dict(item) for item in attempts],
        }
    )


async def generate_document_answer(
    messages: list[dict], images: list[str] | None = None
) -> str:
    """Generate a RAG answer through the bounded approved provider chain."""

    from src.config import settings

    _documentation_provider_trace.set(None)
    attempts: list[dict] = []
    for provider, model in await _documentation_chain():
        last_error: Exception | None = None
        for retry in range(settings.rag_documentation_provider_max_retries + 1):
            t0 = time.perf_counter()
            try:
                result, usage = await provider.generate(
                    messages=messages, model=model, images=images
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Documentation provider %s failed for %s (attempt %d): [%s] %s",
                    provider.provider_id,
                    model,
                    retry + 1,
                    type(exc).__name__,
                    exc,
                )
                continue
            _record_usage(usage)
            attempts.append({"provider": provider.provider_id, "status": "succeeded"})
            _set_documentation_trace(attempts, provider.provider_id)
            logger.info(
                "Documentation response from %s/%s (%.0fms)",
                provider.provider_id,
                model,
                (time.perf_counter() - t0) * 1000,
            )
            return result
        attempts.append({"provider": provider.provider_id, "status": "failed"})
        _set_documentation_trace(attempts, None)
        logger.warning(
            "Documentation provider %s exhausted retry budget: %s",
            provider.provider_id,
            type(last_error).__name__ if last_error else "unknown_error",
        )
    raise RuntimeError("All approved documentation providers are temporarily unavailable.")


async def generate_document_answer_stream(
    messages: list[dict], images: list[str] | None = None
) -> AsyncGenerator[str, None]:
    """Stream through the approved chain, switching only before any text exists."""

    from src.config import settings

    _documentation_provider_trace.set(None)
    attempts: list[dict] = []
    for provider, model in await _documentation_chain():
        for retry in range(settings.rag_documentation_provider_max_retries + 1):
            produced = False
            try:
                async for item in provider.stream(messages=messages, model=model, images=images):
                    if isinstance(item, UsageStats):
                        _record_usage(item)
                    else:
                        if item:
                            produced = True
                            yield item
                attempts.append({"provider": provider.provider_id, "status": "succeeded"})
                _set_documentation_trace(attempts, provider.provider_id)
                return
            except Exception as exc:
                logger.warning(
                    "Documentation streaming provider %s failed for %s (attempt %d, produced=%s): [%s] %s",
                    provider.provider_id,
                    model,
                    retry + 1,
                    produced,
                    type(exc).__name__,
                    exc,
                )
                if produced:
                    attempts.append({"provider": provider.provider_id, "status": "failed"})
                    _set_documentation_trace(attempts, None)
                    raise RuntimeError(
                        "Documentation provider failed after answer output began; retry is unsafe."
                    ) from exc
        attempts.append({"provider": provider.provider_id, "status": "failed"})
        _set_documentation_trace(attempts, None)
    raise RuntimeError("All approved documentation providers are temporarily unavailable.")


async def cleanup_llm_clients() -> None:
    """Close provider HTTP clients. Call on shutdown to avoid connection leaks."""
    await registry.close_all()
    logger.info("LLM clients cleaned up")


async def generate_answer(
    messages: list[dict],
    images: list[str] | None = None,
) -> str:
    """Generate an answer from the selected provider. Raises on provider error."""
    provider, model = await _resolve_selection()
    t0 = time.perf_counter()
    try:
        result, usage = await provider.generate(messages=messages, model=model, images=images)
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        logger.warning(
            "%s failed for model %s (%.0fms): [%s] %s",
            provider.provider_id, model, elapsed, type(e).__name__, e,
        )
        raise RuntimeError(
            f"LLM provider '{provider.provider_id}' failed for model '{model}': "
            f"[{type(e).__name__}] {e}"
        ) from e
    elapsed = (time.perf_counter() - t0) * 1000
    logger.info(
        "LLM response from %s/%s (%.0fms, %d in / %d out tokens, %d in cached)",
        provider.provider_id, model, elapsed,
        usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
    )
    _record_usage(usage)
    return result


async def generate_answer_stream(
    messages: list[dict],
    images: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream tokens from the selected provider. After exhaustion, call
    get_last_usage() for stats. Raises on provider error."""
    provider, model = await _resolve_selection()
    try:
        async for item in provider.stream(messages=messages, model=model, images=images):
            if isinstance(item, UsageStats):
                _record_usage(item)
                logger.info(
                    "LLM streaming from %s/%s (%dms, %d in / %d out tokens, %d in cached)",
                    provider.provider_id, model, item.latency_ms,
                    item.input_tokens, item.output_tokens, item.cached_input_tokens,
                )
            else:
                yield item
    except Exception as e:
        logger.warning(
            "%s streaming failed for model %s: [%s] %s",
            provider.provider_id, model, type(e).__name__, e,
        )
        raise RuntimeError(
            f"LLM provider '{provider.provider_id}' failed (streaming) for model "
            f"'{model}': [{type(e).__name__}] {e}"
        ) from e


async def generate_answer_with_runtime_tools(
    messages: list[dict],
    runtime_tools: list[RuntimeToolDefinition],
    images: list[str] | None = None,
) -> ToolAwareLLMResponse:
    """Tool-aware variant for the runtime tool-use loop.

    If the selected provider has no tool support, or its call fails, returns
    no_tool_support=True so the caller can fall back to generate_answer() without
    claiming live data.
    """
    provider, model = await _resolve_selection()
    if not provider.supports_tools:
        return ToolAwareLLMResponse(provider=provider.provider_id, no_tool_support=True)
    try:
        response = await provider.generate_with_tools(
            model=model, messages=messages, runtime_tools=runtime_tools, images=images
        )
    except Exception as e:
        logger.warning(
            "%s tool-aware call failed for model %s: [%s] %s",
            provider.provider_id, model, type(e).__name__, e,
        )
        return ToolAwareLLMResponse(provider=provider.provider_id, no_tool_support=True)
    if response.usage:
        _record_usage(response.usage)
        # The dominant chat path runs through here several times per turn, so the
        # prompt-cache hit rate is logged per call: it is the only place the
        # re-billed-prefix cost of an agent turn is directly observable.
        logger.info(
            "LLM tool-aware from %s/%s (%dms, %d in / %d out tokens, "
            "%d in cached = %.0f%%, %d tools)",
            provider.provider_id, model, response.usage.latency_ms,
            response.usage.input_tokens, response.usage.output_tokens,
            response.usage.cached_input_tokens,
            response.usage.cache_hit_ratio * 100,
            len(runtime_tools),
        )
    return response
