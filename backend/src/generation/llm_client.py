from __future__ import annotations

import logging

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)


async def generate_answer(
    messages: list[dict[str, str]],
) -> str:
    """Send messages to Ollama LLM and return the full response text."""
    client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
    response = await client.chat(
        model=settings.ollama_llm_model,
        messages=messages,
    )
    return response["message"]["content"]
