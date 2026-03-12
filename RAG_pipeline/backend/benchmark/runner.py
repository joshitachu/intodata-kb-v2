"""Benchmark runner — executes Q&A pairs against the RAG pipeline and scores results."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import BenchmarkRun
from src.generation.llm_client import get_last_usage
from src.generation.rag_chain import run_rag_query
from src.schemas import QueryRequest

from benchmark.judge import judge_answer

logger = logging.getLogger(__name__)


def load_corpus(path: str | Path) -> dict:
    """Load a benchmark corpus JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def compute_precision_at_k(
    retrieved_sources: list[str],
    expected_sources: list[str],
    k: int = 5,
) -> float:
    """Compute precision@k: fraction of top-k retrieved sources that are relevant.

    Uses substring matching — a retrieved source matches if any expected source
    appears as a substring (case-insensitive).
    """
    if not expected_sources:
        return 1.0  # No expected sources = any result is fine

    top_k = retrieved_sources[:k]
    if not top_k:
        return 0.0

    hits = 0
    for source in top_k:
        source_lower = source.lower()
        if any(exp.lower() in source_lower for exp in expected_sources):
            hits += 1
    return hits / len(top_k)


async def run_benchmark(
    corpus_path: str | Path,
    tag: str,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    user_groups: list[str] | None = None,
    user_id: int = 1,
    skip_judge: bool = False,
) -> BenchmarkRun:
    """Execute all corpus questions against the RAG pipeline and store results.

    Args:
        corpus_path: Path to corpus JSON file.
        tag: Tag for this run (e.g. "baseline", "v2-voyage").
        qdrant: Qdrant client.
        db: Database session.
        user_groups: User groups for RBAC filtering. Defaults to ["admin"].
        user_id: User ID for the query. Defaults to 1 (admin).
        skip_judge: If True, skip LLM-as-judge scoring (faster, retrieval-only metrics).
    """
    if user_groups is None:
        user_groups = ["admin"]

    corpus = load_corpus(corpus_path)
    entries = corpus.get("entries", [])
    logger.info(f"Loaded corpus '{corpus['name']}' with {len(entries)} entries")

    results = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for i, entry in enumerate(entries, 1):
        qid = entry["id"]
        question = entry["question"]
        expected = entry.get("expected_answer", "")
        expected_sources = entry.get("expected_sources", [])

        logger.info(f"[{i}/{len(entries)}] Running: {qid} — {question[:60]}")

        start = time.perf_counter()
        try:
            request = QueryRequest(query=question, top_k=5, threshold=0.0)
            response = await run_rag_query(
                request=request,
                user_groups=user_groups,
                user_id=user_id,
                qdrant=qdrant,
                db=db,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            # Extract source filenames
            retrieved_sources = [s.filename for s in response.sources]
            precision = compute_precision_at_k(retrieved_sources, expected_sources, k=5)

            # Get usage stats
            usage = get_last_usage()
            input_tokens = usage.input_tokens if usage else 0
            output_tokens = usage.output_tokens if usage else 0
            cost = usage.cost_usd if usage else 0.0

            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_cost += cost

            # Judge answer relevance
            relevance = {"score": 0, "reason": "skipped"}
            if not skip_judge and expected:
                relevance = await judge_answer(question, expected, response.answer)

            result = {
                "id": qid,
                "question": question,
                "expected_answer": expected,
                "actual_answer": response.answer,
                "retrieved_sources": retrieved_sources,
                "expected_sources": expected_sources,
                "precision_at_5": precision,
                "relevance_score": relevance["score"],
                "relevance_reason": relevance["reason"],
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "category": entry.get("category", ""),
                "difficulty": entry.get("difficulty", ""),
            }
        except Exception as e:
            logger.error(f"Error on {qid}: {e}")
            latency_ms = int((time.perf_counter() - start) * 1000)
            result = {
                "id": qid,
                "question": question,
                "error": str(e),
                "latency_ms": latency_ms,
                "precision_at_5": 0.0,
                "relevance_score": 0,
                "relevance_reason": f"Error: {e}",
            }

        results.append(result)
        logger.info(
            f"  -> P@5={result.get('precision_at_5', 0):.2f} "
            f"Rel={result.get('relevance_score', 0)} "
            f"Lat={result.get('latency_ms', 0)}ms"
        )

    # Compute aggregates
    valid = [r for r in results if "error" not in r]
    avg_precision = sum(r["precision_at_5"] for r in valid) / len(valid) if valid else 0.0
    avg_relevance = sum(r["relevance_score"] for r in valid) / len(valid) if valid else 0.0
    avg_latency = sum(r["latency_ms"] for r in results) / len(results) if results else 0.0

    # Store in DB
    run = BenchmarkRun(
        tag=tag,
        corpus_name=corpus.get("name", "unknown"),
        corpus_version=corpus.get("version", "1.0"),
        llm_model=settings.kimi_llm_model if "kimi" in settings.llm_providers else settings.ollama_llm_model,
        embed_model=settings.voyage_embed_model if "voyage" in settings.embedding_providers else settings.ollama_embed_model,
        reranker_model=settings.reranker_model if settings.reranker_enabled else None,
        num_questions=len(entries),
        avg_precision_at_5=avg_precision,
        avg_relevance_score=avg_relevance,
        avg_latency_ms=avg_latency,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost,
        results=results,
    )
    db.add(run)
    await db.flush()

    logger.info(
        f"\nBenchmark complete: {tag}\n"
        f"  Questions: {len(entries)}\n"
        f"  Avg P@5: {avg_precision:.3f}\n"
        f"  Avg Relevance: {avg_relevance:.2f}/5\n"
        f"  Avg Latency: {avg_latency:.0f}ms\n"
        f"  Total Cost: ${total_cost:.4f}\n"
        f"  Tokens: {total_input_tokens} in / {total_output_tokens} out"
    )

    return run
