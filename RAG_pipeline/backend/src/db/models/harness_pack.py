"""Durable chat plugin-pack draft and reference-attachment records."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


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
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    # A saved pack lives in MCP, so a binding only ever targets a draft. The
    # old one-of-two-targets check constraint had nothing left to choose between.
    __table_args__ = (
        UniqueConstraint("draft_id", "blob_id", "purpose", name="uq_harness_pack_attachment_draft_blob_purpose"),
        Index("ix_harness_pack_attachment_bindings_draft", "draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blob_id: Mapped[int] = mapped_column(
        ForeignKey("harness_pack_attachment_blobs.id", ondelete="RESTRICT"), nullable=False
    )
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("harness_pack_drafts.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
