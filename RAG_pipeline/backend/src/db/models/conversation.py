from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Conversation(Base):
    """A conversation session containing multiple messages."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200))  # Auto-generated from first query
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.id",
        passive_deletes=True,
    )


class ConversationMessage(Base):
    """A single message within a conversation (user or assistant)."""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[dict | None] = mapped_column(JSON)  # For assistant messages
    image_data: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Compact, sanitized references to runtime executions this turn surfaced (tool name
    # + execution_id/job_name/state/headline). Replayed into later turns' context so
    # follow-ups can resolve "that execution". Never holds raw logs/secrets.
    runtime_tool_summary: Mapped[list | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20),
        default="completed",
        server_default="completed",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_source_layer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    doc_gap: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
