"""Immutable custom Pack versions and project activation snapshots."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class HarnessCustomPackVersionRow(Base):
    """One immutable, data-only Pack manifest version owned by a project."""

    __tablename__ = "harness_custom_pack_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "pack_id", "version", name="uq_harness_custom_pack_version"),
        UniqueConstraint("content_hash", name="uq_harness_custom_pack_content_hash"),
        Index("ix_harness_custom_pack_versions_project_pack", "project_id", "pack_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HarnessPackActivationSnapshotRow(Base):
    """Immutable resolved manifest content for one explicit selection."""

    __tablename__ = "harness_pack_activation_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_hash", name="uq_harness_pack_activation_snapshot_hash"),
        Index("ix_harness_pack_activation_snapshots_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_key: Mapped[str] = mapped_column(String(80), nullable=False)
    active_pack_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    resolved_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    resolved_manifests: Mapped[list] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HarnessProjectPackActivationRow(Base):
    """Current immutable Pack snapshot pointer for a project/workflow scope."""

    __tablename__ = "harness_project_pack_activations"
    __table_args__ = (
        UniqueConstraint("project_id", "workflow_key", name="uq_harness_project_pack_activation"),
        Index("ix_harness_project_pack_activations_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_key: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("harness_pack_activation_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
