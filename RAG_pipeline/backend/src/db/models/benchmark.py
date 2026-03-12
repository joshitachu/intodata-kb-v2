"""Benchmark result models."""

from __future__ import annotations

from sqlalchemy import Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from src.db.base import Base


class BenchmarkRun(Base):
    """A single benchmark run (collection of evaluated questions)."""

    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(100), index=True)  # e.g. "baseline", "v2-voyage"
    corpus_name: Mapped[str] = mapped_column(String(200))
    corpus_version: Mapped[str] = mapped_column(String(50))

    # Model config at time of run
    llm_model: Mapped[str] = mapped_column(String(100))
    embed_model: Mapped[str] = mapped_column(String(100))
    reranker_model: Mapped[str | None] = mapped_column(String(100))

    # Aggregate metrics
    num_questions: Mapped[int] = mapped_column(Integer, default=0)
    avg_precision_at_5: Mapped[float | None] = mapped_column(Float)
    avg_relevance_score: Mapped[float | None] = mapped_column(Float)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Full results (JSON array of per-question results)
    results: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
