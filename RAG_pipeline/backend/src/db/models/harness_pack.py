"""Governed Pack persistence and quarantined legacy Pack records."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class HarnessCustomPackVersionRow(Base):
    """Legacy broad custom Pack content. It is deliberately not activatable."""

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
    governance_state: Mapped[str] = mapped_column(String(24), nullable=False, default="legacy")
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HarnessOrganizationGovernancePolicyRow(Base):
    """Persisted organization policy; ``organization_id`` is the future tenancy seam."""

    __tablename__ = "harness_organization_governance_policies"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_harness_org_governance_policy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    policy: Mapped[dict] = mapped_column(JSON, nullable=False)


class HarnessStylePackVersionRow(Base):
    """Immutable presentation-only Style Pack versions in user or project scope."""

    __tablename__ = "harness_style_pack_versions"
    __table_args__ = (
        CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_harness_style_pack_quality_score",
        ),
        CheckConstraint(
            "evaluation_score IS NULL OR (evaluation_score >= 0 AND evaluation_score <= 1)",
            name="ck_harness_style_pack_evaluation_score",
        ),
        Index("ix_harness_style_pack_versions_scope_owner", "scope", "owner_user_id"),
        Index("ix_harness_style_pack_versions_project", "project_id", "pack_id"),
        Index(
            "uq_harness_style_user_pack_version",
            "owner_user_id",
            "pack_id",
            "version",
            unique=True,
            postgresql_where=text("scope = 'user'"),
            sqlite_where=text("scope = 'user'"),
        ),
        Index(
            "uq_harness_style_project_pack_version",
            "project_id",
            "pack_id",
            "version",
            unique=True,
            postgresql_where=text("scope = 'project'"),
            sqlite_where=text("scope = 'project'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    presentation: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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
    stack_selection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compiled_stack_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compiled_stack_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compiled_presentation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    private_version_receipts: Mapped[list | None] = mapped_column(JSON, nullable=True)
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


class HarnessPackDraftRow(Base):
    """Owner-private mutable V3 Analysis Pack draft, optionally chat-linked."""

    __tablename__ = "harness_pack_drafts"
    __table_args__ = (
        Index("ix_harness_pack_drafts_conversation_owner", "conversation_id", "owner_user_id"),
        Index("ix_harness_pack_drafts_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    artifact: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Bounded, non-executable discovery turns retained between builder assists.
    discovery_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # A fork keeps writing new versions to the same private Pack identity.
    target_private_pack_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_private_analysis_packs.id", ondelete="RESTRICT"), nullable=True
    )
    base_private_pack_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessPrivateAnalysisPackRow(Base):
    """Logical owner-only Pack identity with immutable V3 versions."""

    __tablename__ = "harness_private_analysis_packs"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "pack_id", name="uq_harness_private_analysis_pack_owner_id"
        ),
        Index("ix_harness_private_analysis_packs_owner", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessPrivateAnalysisPackVersionRow(Base):
    __tablename__ = "harness_private_analysis_pack_versions"
    __table_args__ = (
        UniqueConstraint("private_pack_id", "version", name="uq_harness_private_analysis_pack_version"),
        Index("ix_harness_private_analysis_pack_versions_pack", "private_pack_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    private_pack_id: Mapped[int] = mapped_column(
        ForeignKey("harness_private_analysis_packs.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact: Mapped[dict] = mapped_column(JSON, nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessPackAttachmentBlobRow(Base):
    """Deduplicated, owner-private Pack reference bytes and bounded extraction."""

    __tablename__ = "harness_pack_attachment_blobs"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "sha256", name="uq_harness_pack_attachment_blob_owner_sha256"),
        Index("ix_harness_pack_attachment_blobs_owner", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class HarnessPackAttachmentBindingRow(Base):
    """A purpose-labeled reference owned by exactly one mutable draft or version."""

    __tablename__ = "harness_pack_attachment_bindings"
    __table_args__ = (
        CheckConstraint(
            "(draft_id IS NOT NULL AND private_pack_version_id IS NULL) OR "
            "(draft_id IS NULL AND private_pack_version_id IS NOT NULL)",
            name="ck_harness_pack_attachment_binding_one_target",
        ),
        UniqueConstraint("draft_id", "blob_id", "purpose", name="uq_harness_pack_attachment_draft_blob_purpose"),
        UniqueConstraint("private_pack_version_id", "blob_id", "purpose", name="uq_harness_pack_attachment_version_blob_purpose"),
        Index("ix_harness_pack_attachment_bindings_draft", "draft_id"),
        Index("ix_harness_pack_attachment_bindings_version", "private_pack_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blob_id: Mapped[int] = mapped_column(
        ForeignKey("harness_pack_attachment_blobs.id", ondelete="RESTRICT"), nullable=False
    )
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_pack_drafts.id", ondelete="CASCADE"), nullable=True
    )
    private_pack_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)


class HarnessPrivateAnalysisPackShareRow(Base):
    __tablename__ = "harness_private_analysis_pack_shares"
    __table_args__ = (UniqueConstraint("private_pack_version_id", "project_id", name="uq_harness_private_analysis_pack_share"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    private_pack_version_id: Mapped[int] = mapped_column(
        ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shared_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessPackPreviewReceiptRow(Base):
    __tablename__ = "harness_pack_preview_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    private_pack_version_id: Mapped[int] = mapped_column(ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    receipt: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessPackPromotionProposalRow(Base):
    __tablename__ = "harness_pack_promotion_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    private_pack_version_id: Mapped[int] = mapped_column(ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    proposed_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
