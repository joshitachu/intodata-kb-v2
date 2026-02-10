from __future__ import annotations

import logging
import time

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768  # nomic-embed-text dimension


async def embed_text(text: str) -> list[float]:
    """Generate embedding vector for a single text using Ollama."""
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    start = time.perf_counter()
    response = await client.embed(model=settings.ollama_embed_model, input=text)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    embedding = response["embeddings"][0]
    logger.info(f"Embedded text in {elapsed_ms}ms (model: {settings.ollama_embed_model}, dim: {len(embedding)})")
    return embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts using Ollama (batched)."""
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    start = time.perf_counter()
    response = await client.embed(model=settings.ollama_embed_model, input=texts)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    embeddings = response["embeddings"]
    logger.info(f"Embedded {len(texts)} texts in {elapsed_ms}ms (model: {settings.ollama_embed_model})")
    return embeddings
