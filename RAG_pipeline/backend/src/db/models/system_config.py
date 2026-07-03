"""Company-level onboarding config — one row per deployment.

Set once by the first superadmin through the admin wizard. All subsequent users
read from this row to know what workspace/platforms are configured.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, func
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


class InstanceSetting(Base):
    """Per-instance model override (one row per key).

    Lets a superadmin change the default LLM/embedder model at runtime without
    editing env vars or restarting the service. Each VPS owns its own Postgres,
    so a row here is naturally scoped to one VPS deployment.

    `key` is a registered constant in
    `src.services.instance_settings_service.SETTING_KEYS`. The dashboard PUTs a
    full {key: value} map and the service writes one row per key, replacing any
    prior value. An empty string value deletes the row.
    """

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    updated_by_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
