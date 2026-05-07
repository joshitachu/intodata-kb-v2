"""User feedback on assistant messages.

Append-only log of thumbs-up / thumbs-down / correction events. Latest row per
(user_id, message_id) represents the user's current reaction; earlier rows are
preserved for analytics and the Week-3 knowledge-gaps admin view.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    # 'up' | 'down' | 'correction' | 'cleared'
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations_snapshot: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
