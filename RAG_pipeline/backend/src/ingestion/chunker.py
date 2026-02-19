from __future__ import annotations

import logging

import tiktoken

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 256  # tokens
DEFAULT_OVERLAP = 32  # tokens
ENCODING_NAME = "cl100k_base"  # tiktoken encoding (works for most models)


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks based on token count."""
    if not text.strip():
        return []

    enc = tiktoken.get_encoding(ENCODING_NAME)
    tokens = enc.encode(text)
    total = len(tokens)

    if total <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        chunk_tokens = tokens[start:end]
        chunk_text_str = enc.decode(chunk_tokens)
        chunks.append(chunk_text_str)

        if end >= total:
            break
        start += chunk_size - overlap

    avg_size = total // len(chunks) if chunks else 0
    logger.info(f"Chunked {total} tokens into {len(chunks)} chunks (avg {avg_size} tokens/chunk)")
    return chunks
