"""Company-level onboarding config — one row per deployment.

Set once by the first superadmin through the admin wizard. All subsequent users
read from this row to know what workspace/platforms are configured.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class SystemConfig(Base):
    """Single-row deployment config written by the admin wizard."""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    workspace_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    selected_platforms: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    selected_comm_tools: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)

    company_wizard_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    company_wizard_completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    wizard_current_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    setup_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )  # pending | processing | completed | failed | skipped
    setup_error: Mapped[str | None] = mapped_column(Text, nullable=True)
