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
    "get_last_usage",
    "cleanup_llm_clients",
]

# Usage from the last completed call, for callers that read stats after the fact
# (streaming exhausts the generator before stats are available).
_last_usage: UsageStats | None = None


def get_last_usage() -> UsageStats | None:
    """Return usage stats from the last completed generate call."""
    return _last_usage


async def _resolve_selection() -> tuple:
    """Resolve the active selection to (provider, bare_model_name)."""
    model_id = await get_rag_chat_model_id()
    provider, bare_model = registry.resolve(model_id)
    return provider, bare_model


async def cleanup_llm_clients() -> None:
    """Close provider HTTP clients. Call on shutdown to avoid connection leaks."""
    await registry.close_all()
    logger.info("LLM clients cleaned up")


async def generate_answer(
    messages: list[dict],
    images: list[str] | None = None,
) -> str:
    """Generate an answer from the selected provider. Raises on provider error."""
    global _last_usage
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
        "LLM response from %s/%s (%.0fms, %d in / %d out tokens)",
        provider.provider_id, model, elapsed, usage.input_tokens, usage.output_tokens,
    )
    _last_usage = usage
    return result


async def generate_answer_stream(
    messages: list[dict],
    images: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream tokens from the selected provider. After exhaustion, call
    get_last_usage() for stats. Raises on provider error."""
    global _last_usage
    provider, model = await _resolve_selection()
    try:
        async for item in provider.stream(messages=messages, model=model, images=images):
            if isinstance(item, UsageStats):
                _last_usage = item
                logger.info(
                    "LLM streaming from %s/%s (%dms, %d in / %d out tokens)",
                    provider.provider_id, model, item.latency_ms,
                    item.input_tokens, item.output_tokens,
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
    global _last_usage
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
        _last_usage = response.usage
    return response
