from __future__ import annotations

import logging
import re

import tiktoken

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 256  # tokens
DEFAULT_OVERLAP = 100  # tokens
ENCODING_NAME = "cl100k_base"  # tiktoken encoding (works for most models)

# Sentence-ending punctuation followed by a space or end-of-string
_SENTENCE_END = re.compile(r"[.?!]\s")


def _snap_to_sentence_boundary(
    text: str,
    enc: tiktoken.Encoding,
    token_count: int,
) -> int:
    """Try to find a sentence boundary within the last 20% of the chunk.

    Returns the token count to use (possibly shortened to end at a sentence).
    """
    if token_count < 5:
        return token_count

    # Look at the last 20% of the decoded text for sentence endings
    threshold = int(token_count * 0.8)
    tokens = enc.encode(text)[:token_count]
    decoded = enc.decode(tokens)

    # Search for last sentence ending in the text
    last_match = None
    for m in _SENTENCE_END.finditer(decoded):
        last_match = m

    if last_match is None:
        return token_count

    # Only snap if the boundary is in the last 20% of the chunk
    boundary_pos = last_match.end()
    boundary_text = decoded[:boundary_pos]
    boundary_tokens = len(enc.encode(boundary_text))

    if boundary_tokens >= threshold:
        return boundary_tokens

    return token_count


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks based on token count.

    Attempts to end chunks at sentence boundaries within the last 20%
    of each chunk to avoid mid-sentence splits.
    """
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

        if end < total:
            # Try to snap to a sentence boundary
            snapped = _snap_to_sentence_boundary(chunk_text_str, enc, len(chunk_tokens))
            if snapped < len(chunk_tokens):
                chunk_tokens = tokens[start:start + snapped]
                chunk_text_str = enc.decode(chunk_tokens)
                end = start + snapped

        chunks.append(chunk_text_str)

        if end >= total:
            break
        start = end - overlap

    avg_size = total // len(chunks) if chunks else 0
    logger.info(f"Chunked {total} tokens into {len(chunks)} chunks (avg {avg_size} tokens/chunk)")
    return chunks
