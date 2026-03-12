"""Benchmark results API endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from src.api.dependencies import DBSession
from src.db.models import BenchmarkRun

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.get("/runs")
async def list_runs(db: DBSession):
    """List all benchmark runs (newest first)."""
    result = await db.execute(
        select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc())
    )
    runs = result.scalars().all()
    return {
        "runs": [
            {
                "id": r.id,
                "tag": r.tag,
                "corpus_name": r.corpus_name,
                "corpus_version": r.corpus_version,
                "llm_model": r.llm_model,
                "embed_model": r.embed_model,
                "reranker_model": r.reranker_model,
                "num_questions": r.num_questions,
                "avg_precision_at_5": r.avg_precision_at_5,
                "avg_relevance_score": r.avg_relevance_score,
                "avg_latency_ms": r.avg_latency_ms,
                "total_input_tokens": r.total_input_tokens,
                "total_output_tokens": r.total_output_tokens,
                "total_cost_usd": r.total_cost_usd,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ],
        "total": len(runs),
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: DBSession):
    """Get details of a specific benchmark run including per-question results."""
    run = await db.get(BenchmarkRun, run_id)
    if not run:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Benchmark run {run_id} not found")
    return {
        "id": run.id,
        "tag": run.tag,
        "corpus_name": run.corpus_name,
        "corpus_version": run.corpus_version,
        "llm_model": run.llm_model,
        "embed_model": run.embed_model,
        "reranker_model": run.reranker_model,
        "num_questions": run.num_questions,
        "avg_precision_at_5": run.avg_precision_at_5,
        "avg_relevance_score": run.avg_relevance_score,
        "avg_latency_ms": run.avg_latency_ms,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_cost_usd": run.total_cost_usd,
        "results": run.results,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
