"""Durable, sanitized evidence snapshots collected by harness workflows."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class HarnessEvidenceRow(Base):
    """A safe point-in-time observation, never a provider's raw payload."""

    __tablename__ = "harness_evidence"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "collected_order", name="uq_harness_evidence_run_collected_order"
        ),
        UniqueConstraint(
            "workflow_run_id", "used_order", name="uq_harness_evidence_run_used_order"
        ),
        CheckConstraint("freshness >= 0 AND freshness <= 1", name="ck_harness_evidence_freshness"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_harness_evidence_confidence"
        ),
        CheckConstraint("authority >= 0 AND authority <= 1", name="ck_harness_evidence_authority"),
        Index("ix_harness_evidence_workflow_run_id", "workflow_run_id"),
        Index("ix_harness_evidence_claim_key", "claim_key"),
        Index("ix_harness_evidence_conflict_group", "conflict_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_class: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    project_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connector_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    observed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    authority: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    claim_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    conflict_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    conflict_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    collected_order: Mapped[int] = mapped_column(Integer, nullable=False)
    used_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safe_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
