from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


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
    __table_args__ = (
        Index("ix_conversation_messages_workflow_run_id", "workflow_run_id"),
        UniqueConstraint(
            "conversation_id",
            "client_turn_id",
            name="uq_conversation_messages_client_turn",
        ),
        UniqueConstraint(
            "in_reply_to_message_id",
            name="uq_conversation_messages_in_reply_to",
        ),
    )

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
    client_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_turn_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    in_reply_to_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    # Validated, provider-neutral routing snapshot. It contains policy and scope,
    # never prompts, credentials, raw logs, or hidden reasoning.
    turn_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Typed, user-visible answer envelope. This is separate from workflow
    # activity and stores only sanitized sections, citations, and statuses.
    structured_answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
