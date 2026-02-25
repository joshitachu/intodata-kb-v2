from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

import httpx
import ollama as ollama_lib

from src.config import settings

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


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def _kimi_generate(messages: list[dict[str, str]]) -> str:
    client = await _get_kimi_client()
    response = await client.chat.completions.create(
        model=settings.kimi_llm_model,
        messages=messages,
        temperature=settings.kimi_temperature,
        max_tokens=2048,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return response.choices[0].message.content


async def _kimi_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
    client = await _get_kimi_client()
    stream = await client.chat.completions.create(
        model=settings.kimi_llm_model,
        messages=messages,
        temperature=settings.kimi_temperature,
        max_tokens=2048,
        stream=True,
        extra_body={"thinking": {"type": "disabled"}},
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token


async def _ollama_generate(messages: list[dict[str, str]]) -> str:
    client = await _get_ollama_client()
    response = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
    )
    return response["message"]["content"]


async def _ollama_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
    client = await _get_ollama_client()
    stream = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        token = chunk["message"]["content"]
        if token:
            yield token


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
# Public API — same signatures as before
# ---------------------------------------------------------------------------


async def generate_answer(
    messages: list[dict[str, str]],
) -> str:
    """Try each provider in chain order. First success wins."""
    errors: list[str] = []
    for name in settings.llm_providers:
        gen_fn = _resolve(name, 0)
        try:
            t0 = time.perf_counter()
            result = await gen_fn(messages)
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("LLM response from %s (%.0fms)", name, elapsed)
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning("%s failed (%.0fms): %s", name, elapsed, e)
            errors.append(f"{name}: {e}")
            continue
    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


async def generate_answer_stream(
    messages: list[dict[str, str]],
) -> AsyncGenerator[str, None]:
    """Try each provider in chain order for streaming."""
    errors: list[str] = []
    for name in settings.llm_providers:
        stream_fn = _resolve(name, 1)
        try:
            async for token in stream_fn(messages):
                yield token
            logger.info("LLM streaming response from %s", name)
            return
        except Exception as e:
            logger.warning("%s streaming failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue
    raise RuntimeError(f"All LLM providers failed (streaming): {'; '.join(errors)}")
