from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

import httpx
import ollama as ollama_lib

from src.config import settings
from src.generation.usage import UsageStats, StreamUsageStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton clients (lazy-initialized, one per process)
# ---------------------------------------------------------------------------

_kimi_client = None
_kimi_lock = asyncio.Lock()

_ollama_client = None
_ollama_lock = asyncio.Lock()


async def _get_kimi_client():
    global _kimi_client
    if _kimi_client is None:
        async with _kimi_lock:
            if _kimi_client is None:
                from openai import AsyncOpenAI

                _kimi_client = AsyncOpenAI(
                    base_url="https://api.moonshot.ai/v1",
                    api_key=settings.kimi_api_key,
                    timeout=httpx.Timeout(settings.kimi_timeout, connect=5.0),
                    max_retries=0,  # Our provider chain handles fallback — don't let SDK retry internally
                )
    return _kimi_client


async def _get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        async with _ollama_lock:
            if _ollama_client is None:
                _ollama_client = ollama_lib.AsyncClient(
                    host=settings.ollama_base_url,
                    timeout=httpx.Timeout(settings.ollama_timeout, connect=5.0),
                )
    return _ollama_client


async def cleanup_llm_clients():
    """Close singleton HTTP clients. Call on shutdown to avoid connection leaks."""
    global _kimi_client, _ollama_client
    if _kimi_client is not None:
        try:
            await _kimi_client.close()
        except Exception:
            pass
        _kimi_client = None
    if _ollama_client is not None:
        try:
            await _ollama_client._client.aclose()
        except Exception:
            pass
        _ollama_client = None
    logger.info("LLM clients cleaned up")


# ---------------------------------------------------------------------------
# Provider implementations (with usage extraction)
# ---------------------------------------------------------------------------


async def _kimi_generate(messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
    client = await _get_kimi_client()
    t0 = time.perf_counter()
    response = await client.chat.completions.create(
        model=settings.kimi_llm_model,
        messages=messages,
        temperature=settings.kimi_temperature,
        max_tokens=2048,
        extra_body={"thinking": {"type": "disabled"}},
    )
    elapsed = int((time.perf_counter() - t0) * 1000)
    usage = UsageStats(
        provider="kimi",
        model=settings.kimi_llm_model,
        latency_ms=elapsed,
    )
    if response.usage:
        usage.input_tokens = response.usage.prompt_tokens or 0
        usage.output_tokens = response.usage.completion_tokens or 0
        usage.calculate_cost()
    return response.choices[0].message.content, usage


async def _kimi_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str | UsageStats, None]:
    client = await _get_kimi_client()
    t0 = time.perf_counter()
    stream = await client.chat.completions.create(
        model=settings.kimi_llm_model,
        messages=messages,
        temperature=settings.kimi_temperature,
        max_tokens=2048,
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"thinking": {"type": "disabled"}},
    )
    acc = StreamUsageStats(provider="kimi", model=settings.kimi_llm_model)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        # OpenAI-compat APIs send usage in the final chunk
        if chunk.usage:
            acc.input_tokens = chunk.usage.prompt_tokens or 0
            acc.output_tokens = chunk.usage.completion_tokens or 0
    acc.latency_ms = int((time.perf_counter() - t0) * 1000)
    yield acc.finalize()


async def _ollama_generate(messages: list[dict[str, str]]) -> tuple[str, UsageStats]:
    client = await _get_ollama_client()
    t0 = time.perf_counter()
    response = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
    )
    elapsed = int((time.perf_counter() - t0) * 1000)
    usage = UsageStats(
        provider="ollama",
        model=settings.ollama_llm_model,
        input_tokens=response.get("prompt_eval_count", 0) or 0,
        output_tokens=response.get("eval_count", 0) or 0,
        latency_ms=elapsed,
    )
    return response["message"]["content"], usage


async def _ollama_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str | UsageStats, None]:
    client = await _get_ollama_client()
    t0 = time.perf_counter()
    stream = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
        stream=True,
    )
    acc = StreamUsageStats(provider="ollama", model=settings.ollama_llm_model)
    async for chunk in stream:
        token = chunk["message"]["content"]
        if token:
            yield token
        # Ollama sends eval counts in the final chunk (when done=true)
        if chunk.get("done"):
            acc.input_tokens = chunk.get("prompt_eval_count", 0) or 0
            acc.output_tokens = chunk.get("eval_count", 0) or 0
    acc.latency_ms = int((time.perf_counter() - t0) * 1000)
    yield acc.finalize()


# ---------------------------------------------------------------------------
# Provider registry — maps name to (generate_fn_name, stream_fn_name)
# Resolved at call time so patching works in tests.
# ---------------------------------------------------------------------------

_PROVIDER_FUNCS: dict[str, tuple[str, str]] = {
    "kimi": ("_kimi_generate", "_kimi_stream"),
    "ollama": ("_ollama_generate", "_ollama_stream"),
}

import sys as _sys

def _resolve(name: str, idx: int):
    """Resolve a provider function by name at call time."""
    func_name = _PROVIDER_FUNCS[name][idx]
    return getattr(_sys.modules[__name__], func_name)

# ---------------------------------------------------------------------------
# Thread-local usage storage for streaming — allows callers to get stats
# after the generator is exhausted.
# ---------------------------------------------------------------------------

_last_usage: UsageStats | None = None


def get_last_usage() -> UsageStats | None:
    """Return usage stats from the last completed generate call."""
    return _last_usage

# ---------------------------------------------------------------------------
# Public API — backwards-compatible signatures
# ---------------------------------------------------------------------------


async def generate_answer(
    messages: list[dict[str, str]],
) -> str:
    """Try each provider in chain order. First success wins."""
    global _last_usage
    errors: list[str] = []
    for name in settings.llm_providers:
        gen_fn = _resolve(name, 0)
        try:
            t0 = time.perf_counter()
            result, usage = await gen_fn(messages)
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("LLM response from %s (%.0fms, %d in / %d out tokens)",
                        name, elapsed, usage.input_tokens, usage.output_tokens)
            _last_usage = usage
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning("%s failed (%.0fms): [%s] %s", name, elapsed, type(e).__name__, e)
            errors.append(f"{name}: [{type(e).__name__}] {e}")
            continue
    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


async def generate_answer_stream(
    messages: list[dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Try each provider in chain order for streaming.

    Yields string tokens. After exhaustion, call get_last_usage() for stats.
    """
    global _last_usage
    errors: list[str] = []
    for name in settings.llm_providers:
        stream_fn = _resolve(name, 1)
        try:
            async for item in stream_fn(messages):
                if isinstance(item, UsageStats):
                    _last_usage = item
                    logger.info("LLM streaming from %s (%dms, %d in / %d out tokens)",
                                name, item.latency_ms, item.input_tokens, item.output_tokens)
                else:
                    yield item
            return
        except Exception as e:
            logger.warning("%s streaming failed: [%s] %s", name, type(e).__name__, e)
            errors.append(f"{name}: [{type(e).__name__}] {e}")
            continue
    raise RuntimeError(f"All LLM providers failed (streaming): {'; '.join(errors)}")
