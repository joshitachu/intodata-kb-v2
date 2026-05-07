"""Onboarding wizard, product-tour state, and in-app notifications.

Multi-tenancy: we use a "one VPS per client" deployment model, so tenant config
lives in env vars per deployment (subdomain, branding, feature flags) rather
than in a shared DB table.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class UserOnboarding(Base):
    """Per-user onboarding wizard + product-tour state.

    A row is created for each net-new user on admin provisioning (so the wizard
    shows on first login). Existing users (no row) are treated as
    `wizard_completed=True` so they skip the wizard.
    """

    __tablename__ = "user_onboarding"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Workspace — company / workspace name shown in branding
    workspace_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Wizard state
    wizard_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    wizard_completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    wizard_current_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Wizard selections (JSONB arrays)
    selected_platforms: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    selected_comm_tools: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    # Source upload (optional step)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # github | zip
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Background processing status
    setup_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )  # pending | processing | completed | failed | skipped
    setup_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Product tour state
    tour_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    tour_completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tour_current_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])


class Notification(Base):
    """In-app notification for a single user (bell icon)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "setup_complete"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False, index=True
    )
    meta: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
