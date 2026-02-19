from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def _groq_generate(messages: list[dict[str, str]]) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_llm_model,
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
    )
    return response.choices[0].message.content


async def _groq_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    stream = await client.chat.completions.create(
        model=settings.groq_llm_model,
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
        stream=True,
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token


async def _openrouter_generate(messages: list[dict[str, str]]) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )
    response = await client.chat.completions.create(
        model=settings.openrouter_llm_model,
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
    )
    return response.choices[0].message.content


async def _openrouter_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
    )
    stream = await client.chat.completions.create(
        model=settings.openrouter_llm_model,
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
        stream=True,
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token


async def _ollama_generate(messages: list[dict[str, str]]) -> str:
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    response = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
    )
    return response["message"]["content"]


async def _ollama_stream(messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
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
    "groq": ("_groq_generate", "_groq_stream"),
    "openrouter": ("_openrouter_generate", "_openrouter_stream"),
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
            result = await gen_fn(messages)
            logger.info("LLM response from %s", name)
            return result
        except Exception as e:
            logger.warning("%s failed: %s", name, e)
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
