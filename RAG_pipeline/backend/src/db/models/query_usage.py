"""Token/cost tracking for all LLM interactions."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class QueryUsage(Base):
    """Per-query token usage and cost tracking.

    Covers RAG queries, conversations, and deep-dive sessions.
    """

    __tablename__ = "query_usage"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who / what
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    interface: Mapped[str] = mapped_column(
        String(50), index=True
    )  # "rag_query" | "conversation" | "deep_dive" | "doc_generation"

    # Query info
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Token counts
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cost
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Performance
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    search_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Context
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
