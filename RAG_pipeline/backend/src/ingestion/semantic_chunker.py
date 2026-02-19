"""Semantic (structure-aware) chunking for better retrieval quality.

Splits text on paragraph boundaries, merges small paragraphs to stay within
token limits, and falls back to mechanical splitting for oversized blocks.
"""
from __future__ import annotations

import logging
import re

import tiktoken

from src.ingestion.chunker import chunk_text as mechanical_chunk

logger = logging.getLogger(__name__)

ENCODING_NAME = "cl100k_base"
DEFAULT_MAX_TOKENS = 512
MIN_CHUNK_TOKENS = 50

# Patterns that indicate structure boundaries (higher priority = stronger break)
SLIDE_MARKER = re.compile(r"^\[SLIDE \d+\]", re.MULTILINE)
HEADING_MARKER = re.compile(r"^#{1,6}\s", re.MULTILINE)


def chunk_text_semantic(
    text: str,
    file_type: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = MIN_CHUNK_TOKENS,
) -> list[str]:
    """Split text into semantically meaningful chunks.

    Strategy:
    1. XLSX: delegate to mechanical chunker (tables don't benefit from semantic splitting)
    2. Split on paragraph breaks (double newlines) and structural markers
    3. Merge small paragraphs up to max_tokens
    4. Fall back to mechanical splitting for oversized paragraphs
    5. Filter out tiny chunks (< min_tokens)

    Args:
        text: The text to chunk.
        file_type: File extension (pdf, docx, pptx, xlsx).
        max_tokens: Maximum tokens per chunk.
        min_tokens: Minimum tokens per chunk (smaller are filtered out).

    Returns:
        List of text chunks.
    """
    if not text.strip():
        return []

    # XLSX: tables don't benefit from semantic splitting
    if file_type == "xlsx":
        return mechanical_chunk(text, chunk_size=max_tokens, overlap=32)

    enc = tiktoken.get_encoding(ENCODING_NAME)

    # If total text fits in one chunk, return it directly (skip min_tokens filter)
    total_tokens = len(enc.encode(text.strip()))
    if total_tokens <= max_tokens:
        return [text.strip()]

    # Split on structural markers first, then paragraph breaks
    paragraphs = _split_into_paragraphs(text)

    if not paragraphs:
        return []

    # Merge small paragraphs and split oversized ones
    chunks = _merge_and_split(paragraphs, enc, max_tokens, min_tokens)

    total_tokens = sum(len(enc.encode(c)) for c in chunks)
    logger.info(
        f"Semantic chunking: {len(paragraphs)} paragraphs → {len(chunks)} chunks "
        f"(~{total_tokens} tokens, max={max_tokens}, file_type={file_type})"
    )
    return chunks


def _split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on double newlines and structural markers.

    Preserves slide markers [SLIDE N] and heading markers (# Heading) as
    paragraph boundaries.
    """
    # Split on double newlines (paragraph breaks)
    raw_parts = re.split(r"\n{2,}", text)

    paragraphs: list[str] = []
    for part in raw_parts:
        stripped = part.strip()
        if not stripped:
            continue

        # Check for slide markers within the part and split further
        if SLIDE_MARKER.search(stripped):
            sub_parts = SLIDE_MARKER.split(stripped)
            markers = SLIDE_MARKER.findall(stripped)
            for i, sub in enumerate(sub_parts):
                sub = sub.strip()
                if i < len(markers) and sub:
                    # Prepend the marker back to its content
                    paragraphs.append(f"{markers[i]}\n{sub}")
                elif sub:
                    paragraphs.append(sub)
                elif i < len(markers):
                    # Marker with no content after it — will be merged with next
                    paragraphs.append(markers[i])
        else:
            paragraphs.append(stripped)

    return paragraphs


def _merge_and_split(
    paragraphs: list[str],
    enc: tiktoken.Encoding,
    max_tokens: int,
    min_tokens: int,
) -> list[str]:
    """Merge small paragraphs and split oversized ones.

    Walks through paragraphs, accumulating them in a buffer until adding
    the next one would exceed max_tokens. Then flushes the buffer as a chunk.
    Paragraphs that are themselves larger than max_tokens get mechanically split.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    for para in paragraphs:
        para_tokens = len(enc.encode(para))

        # If this single paragraph exceeds max_tokens, flush buffer then split it
        if para_tokens > max_tokens:
            # Flush accumulated buffer first
            if buffer:
                chunk = "\n\n".join(buffer)
                if len(enc.encode(chunk)) >= min_tokens:
                    chunks.append(chunk)
                buffer = []
                buffer_tokens = 0

            # Mechanically split the oversized paragraph
            sub_chunks = mechanical_chunk(para, chunk_size=max_tokens, overlap=32)
            for sc in sub_chunks:
                if len(enc.encode(sc)) >= min_tokens:
                    chunks.append(sc)
            continue

        # Would adding this paragraph exceed the limit?
        if buffer_tokens + para_tokens > max_tokens and buffer:
            # Flush current buffer
            chunk = "\n\n".join(buffer)
            if len(enc.encode(chunk)) >= min_tokens:
                chunks.append(chunk)
            buffer = []
            buffer_tokens = 0

        buffer.append(para)
        buffer_tokens += para_tokens

    # Flush remaining buffer
    if buffer:
        chunk = "\n\n".join(buffer)
        if len(enc.encode(chunk)) >= min_tokens:
            chunks.append(chunk)

    return chunks
