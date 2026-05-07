"""Lightweight wizard telemetry.

Raw event stream capturing what users pick during onboarding. The goal is to
learn which platforms/tools users actually select so we can prioritise which
integration to build end-to-end next. No dashboards, no retention policy —
raw events plus one aggregate endpoint.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


from src.db.base import Base


class WizardEvent(Base):
    __tablename__ = "wizard_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # e.g. "wizard_step_viewed", "wizard_platform_selected", "tour_started"
    event_type: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
