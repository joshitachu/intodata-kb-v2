"""Embedding provider chain: Voyage AI → Ollama fallback.

Uses the OpenAI-compatible API format for Voyage AI (the ``openai`` SDK is
already a project dependency).
"""
from __future__ import annotations

import logging
import time

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)


def get_embedding_dim() -> int:
    """Return the embedding dimension based on the active provider."""
    for provider in settings.embedding_providers:
        if provider == "voyage" and settings.voyage_api_key:
            return settings.voyage_embed_dim
        if provider == "ollama":
            return 768  # nomic-embed-text
    # Fallback
    return settings.voyage_embed_dim if settings.voyage_api_key else 768


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def _voyage_embed_single(text: str) -> list[float]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url="https://api.voyageai.com/v1",
        api_key=settings.voyage_api_key,
    )
    response = await client.embeddings.create(
        model=settings.voyage_embed_model,
        input=text,
    )
    return response.data[0].embedding


async def _voyage_embed_batch(texts: list[str]) -> list[list[float]]:
    import asyncio
    from openai import AsyncOpenAI

    # Voyage paid-tier limits: up to 128 inputs per request, high TPM.
    SUB_BATCH_SIZE = 128
    SUB_BATCH_DELAY = 1  # courtesy delay between sub-batches

    client = AsyncOpenAI(
        base_url="https://api.voyageai.com/v1",
        api_key=settings.voyage_api_key,
    )

    if len(texts) <= SUB_BATCH_SIZE:
        response = await client.embeddings.create(
            model=settings.voyage_embed_model,
            input=texts,
        )
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

    # Process in sub-batches with delays
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), SUB_BATCH_SIZE):
        sub_batch = texts[i : i + SUB_BATCH_SIZE]
        if i > 0:
            logger.info(
                f"Voyage rate-limit pause: waiting {SUB_BATCH_DELAY}s before "
                f"sub-batch {i // SUB_BATCH_SIZE + 1} "
                f"({len(sub_batch)} texts)"
            )
            await asyncio.sleep(SUB_BATCH_DELAY)
        response = await client.embeddings.create(
            model=settings.voyage_embed_model,
            input=sub_batch,
        )
        batch_embeddings = [
            item.embedding
            for item in sorted(response.data, key=lambda x: x.index)
        ]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


async def _ollama_embed_single(text: str) -> list[float]:
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    response = await client.embed(model=settings.ollama_embed_model, input=text)
    return response["embeddings"][0]


async def _ollama_embed_batch(texts: list[str]) -> list[list[float]]:
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    response = await client.embed(model=settings.ollama_embed_model, input=texts)
    return response["embeddings"]


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_EMBED_PROVIDERS: dict[str, tuple[str, str]] = {
    "voyage": ("_voyage_embed_single", "_voyage_embed_batch"),
    "ollama": ("_ollama_embed_single", "_ollama_embed_batch"),
}

import sys as _sys


def _resolve(provider: str, idx: int):
    func_name = _EMBED_PROVIDERS[provider][idx]
    return getattr(_sys.modules[__name__], func_name)


# ---------------------------------------------------------------------------
# Public API — same signatures as before
# ---------------------------------------------------------------------------


async def embed_text(text: str) -> list[float]:
    """Generate embedding vector for a single text using the provider chain."""
    errors: list[str] = []
    start = time.perf_counter()

    for name in settings.embedding_providers:
        if name not in _EMBED_PROVIDERS:
            continue
        fn = _resolve(name, 0)
        try:
            embedding = await fn(text)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                f"Embedded text in {elapsed_ms}ms "
                f"(provider: {name}, dim: {len(embedding)})"
            )
            return embedding
        except Exception as e:
            logger.warning("Embedding provider %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue

    raise RuntimeError(f"All embedding providers failed: {'; '.join(errors)}")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts using the provider chain."""
    errors: list[str] = []
    start = time.perf_counter()

    for name in settings.embedding_providers:
        if name not in _EMBED_PROVIDERS:
            continue
        fn = _resolve(name, 1)
        try:
            embeddings = await fn(texts)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                f"Embedded {len(texts)} texts in {elapsed_ms}ms "
                f"(provider: {name})"
            )
            return embeddings
        except Exception as e:
            logger.warning("Embedding provider %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
            continue

    raise RuntimeError(f"All embedding providers failed: {'; '.join(errors)}")
