"""Map-reduce summarization pipeline for full-document summaries.

When a user asks to summarize a document, the standard top_k=3 retrieval
only returns a few chunks. This module retrieves ALL chunks for a document
and uses a map-reduce strategy:
  1. Identify which document the user means (vector search top_k=1)
  2. Scroll ALL chunks for that document from Qdrant
  3. Map: summarize each batch of chunks independently
  4. Reduce: combine all batch summaries into a final coherent summary
"""
from __future__ import annotations

import asyncio
import logging
import time

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    OrderBy,
)

from src.generation.llm_client import generate_answer
from src.retrieval.embedder import embed_text
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import DocumentSource

logger = logging.getLogger(__name__)

# Max chunks per batch for a single map call
MAP_BATCH_SIZE = 8
# Max concurrent LLM calls to avoid overwhelming Ollama
MAX_CONCURRENT_MAP = 3

MAP_PROMPT = (
    "You are a summarization assistant. "
    "Summarize the following section of a document concisely. "
    "Preserve key facts, decisions, processes, and important details. "
    "Do NOT add information that is not in the text.\n\n"
    "Text to summarize:\n{text}"
)

REDUCE_PROMPT = (
    "You are a summarization assistant. "
    "Below are summaries of different sections of the document '{filename}'. "
    "Combine them into a single, coherent, well-structured summary.\n\n"
    "## Formatting rules\n"
    "- Use **markdown**: headings, bullet lists, bold where appropriate.\n"
    "- Preserve all key facts, decisions, processes, and important details.\n"
    "- Organize logically (not necessarily in the order given).\n"
    "- Be comprehensive but concise — aim for a complete overview.\n"
    "- Cite the source as [1] since this is a single-document summary.\n\n"
    "Section summaries:\n{summaries}"
)


async def resolve_document_id(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
) -> tuple[int, str] | None:
    """Identify which document the user wants to summarize via vector search.

    Returns (document_id, filename) or None if no match found.
    """
    query_embedding = await embed_text(query)

    search_filter = None
    if user_groups:
        search_filter = Filter(
            must=[
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=user_groups),
                )
            ]
        )

    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=search_filter,
        limit=1,
        with_payload=True,
    )

    if not results.points:
        logger.info("resolve_document_id: no results found")
        return None

    point = results.points[0]
    if point.score < 0.3:
        logger.info(f"resolve_document_id: best match score {point.score:.4f} below 0.3 threshold")
        return None

    doc_id = point.payload["document_id"]
    filename = point.payload["filename"]
    logger.info(f"resolve_document_id: matched document_id={doc_id} filename={filename!r} score={point.score:.4f}")
    return (doc_id, filename)


async def retrieve_document_chunks(
    client: AsyncQdrantClient,
    document_id: int,
    user_groups: list[str],
) -> list[str]:
    """Retrieve ALL chunks for a given document_id from Qdrant, sorted by chunk_index.

    Uses Qdrant scroll() to paginate through all chunks. Applies RBAC filter.
    Returns list of chunk texts in order.
    """
    filter_conditions = [
        FieldCondition(key="document_id", match=MatchValue(value=document_id)),
    ]
    if user_groups:
        filter_conditions.append(
            FieldCondition(key="allowed_groups", match=MatchAny(any=user_groups)),
        )

    scroll_filter = Filter(must=filter_conditions)

    all_chunks: list[tuple[int, str]] = []
    offset = None

    while True:
        scroll_kwargs: dict = {
            "collection_name": COLLECTION_NAME,
            "scroll_filter": scroll_filter,
            "limit": 100,
            "with_payload": True,
        }
        if offset is not None:
            scroll_kwargs["offset"] = offset

        points, next_offset = await client.scroll(**scroll_kwargs)

        for point in points:
            chunk_index = point.payload.get("chunk_index", 0)
            text = point.payload.get("text", "")
            all_chunks.append((chunk_index, text))

        if next_offset is None:
            break
        offset = next_offset

    # Sort by chunk_index to preserve document order
    all_chunks.sort(key=lambda x: x[0])
    texts = [text for _, text in all_chunks]
    logger.info(f"retrieve_document_chunks: got {len(texts)} chunks for document_id={document_id}")
    return texts


async def map_summarize(chunks: list[str], batch_index: int) -> str:
    """Summarize a batch of chunks via LLM (one map step)."""
    combined_text = "\n\n".join(chunks)
    prompt = MAP_PROMPT.format(text=combined_text)
    messages = [{"role": "user", "content": prompt}]

    start = time.perf_counter()
    summary = await generate_answer(messages)
    elapsed = int((time.perf_counter() - start) * 1000)
    logger.info(f"map_summarize: batch {batch_index} ({len(chunks)} chunks) completed in {elapsed}ms")
    return summary


async def reduce_summaries(summaries: list[str], filename: str) -> str:
    """Combine all map summaries into a single coherent summary (reduce step)."""
    numbered = "\n\n".join(
        f"--- Section {i + 1} ---\n{s}" for i, s in enumerate(summaries)
    )
    prompt = REDUCE_PROMPT.format(filename=filename, summaries=numbered)
    messages = [{"role": "user", "content": prompt}]

    start = time.perf_counter()
    final = await generate_answer(messages)
    elapsed = int((time.perf_counter() - start) * 1000)
    logger.info(f"reduce_summaries: combined {len(summaries)} summaries in {elapsed}ms")
    return final


async def run_map_reduce(
    client: AsyncQdrantClient,
    document_id: int,
    filename: str,
    user_groups: list[str],
) -> tuple[str, DocumentSource]:
    """Full map-reduce summarization pipeline.

    1. Retrieve all chunks for the document
    2. Batch chunks into groups of MAP_BATCH_SIZE
    3. Run map_summarize on each batch (parallel with semaphore)
    4. Run reduce_summaries to combine all batch summaries

    Returns (answer_text, document_source).
    """
    start = time.perf_counter()

    # 1. Retrieve all chunks
    chunks = await retrieve_document_chunks(client, document_id, user_groups)
    if not chunks:
        logger.warning(f"run_map_reduce: no chunks found for document_id={document_id}")
        return (
            f"I found the document '{filename}' but it appears to have no content to summarize.",
            DocumentSource(
                document_id=document_id,
                filename=filename,
                score=1.0,
                text="",
                reference_number=1,
            ),
        )

    # 2. Batch chunks
    batches: list[list[str]] = []
    for i in range(0, len(chunks), MAP_BATCH_SIZE):
        batches.append(chunks[i : i + MAP_BATCH_SIZE])

    logger.info(
        f"run_map_reduce: {len(chunks)} chunks → {len(batches)} batches "
        f"(batch_size={MAP_BATCH_SIZE})"
    )

    # 3. Map phase — parallel with semaphore
    if len(batches) == 1:
        # Single batch: skip map, go straight to a direct summary
        summaries = [await map_summarize(batches[0], 0)]
    else:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_MAP)

        async def limited_map(batch: list[str], idx: int) -> str:
            async with semaphore:
                return await map_summarize(batch, idx)

        summaries = await asyncio.gather(
            *(limited_map(batch, i) for i, batch in enumerate(batches))
        )

    # 4. Reduce phase
    if len(summaries) == 1:
        answer = summaries[0]
    else:
        answer = await reduce_summaries(list(summaries), filename)

    elapsed = int((time.perf_counter() - start) * 1000)
    logger.info(
        f"run_map_reduce: completed for '{filename}' in {elapsed}ms "
        f"({len(chunks)} chunks, {len(batches)} batches)"
    )

    source = DocumentSource(
        document_id=document_id,
        filename=filename,
        score=1.0,
        text=f"Full document summary ({len(chunks)} chunks)",
        reference_number=1,
    )

    return answer, source
