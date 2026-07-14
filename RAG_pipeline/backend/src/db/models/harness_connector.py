"""Persistence rows for configured generic harness connectors."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class HarnessConnectorInstanceRow(Base):
    __tablename__ = "harness_connector_instances"
    __table_args__ = (Index("ix_harness_connector_instances_provider_key", "provider_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    auth_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    credential_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    health_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    health_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    capability_bindings: Mapped[list["HarnessConnectorCapabilityBindingRow"]] = relationship(
        back_populates="connector_instance", cascade="all, delete-orphan", passive_deletes=True
    )


class HarnessConnectorCapabilityBindingRow(Base):
    __tablename__ = "harness_connector_capability_bindings"
    __table_args__ = (
        UniqueConstraint("connector_instance_id", "capability_id", name="uq_harness_connector_binding"),
        Index("ix_harness_connector_bindings_capability_id", "capability_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_instance_id: Mapped[int] = mapped_column(
        ForeignKey("harness_connector_instances.id", ondelete="CASCADE"), nullable=False
    )
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    risk_tier: Mapped[str] = mapped_column(String(30), nullable=False, default="read_only")
    redaction_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    connector_instance: Mapped[HarnessConnectorInstanceRow] = relationship(
        back_populates="capability_bindings"
    )
