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
    # Embedded assistants share the conversation runtime and persistence, but
    # their threads are not ordinary sidebar chats until the user explicitly
    # promotes one. `origin_surface` is immutable provenance;
    # `visible_in_chat` is the user-controlled promotion state.
    origin_surface: Mapped[str] = mapped_column(
        String(20), default="chat", server_default="chat"
    )
    visible_in_chat: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
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
    # Compact, sanitized record of what the turn executed. Two entry shapes, both
    # reference-only and never holding raw logs, arguments or secrets:
    #   - runtime execution references (tool_name + execution_id/job_name/state/
    #     headline), replayed into later turns so follow-ups can resolve "that
    #     execution" -- see generation.agent_loop.build_runtime_tool_summary;
    #   - harness capability calls (capability_id + status + call_count and, when
    #     applicable, evidence_count/truncated/error_code) -- see
    #     harness.turn_provenance.build_runtime_capability_summary.
    runtime_tool_summary: Mapped[list | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20),
        default="completed",
        server_default="completed",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which source layer primarily supported this answer. Allowed values are the
    # document corpus partitions from the chunk `source_layer` payload key
    # (canonical_docs, technical_analysis, support_maps, scorecard,
    # evidence_slices) when the answer cites indexed documents, and otherwise the
    # evidence families from harness.evidence_summary.EvidenceFamily (topology,
    # runtime, documentation, metadata, conversation, connector, other). NULL means
    # "not determinable for this turn" -- no evidence, or a tie between families.
    # An unlabelled turn is intended; a mislabelled one defeats the column.
    primary_source_layer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # True when the primary source could not answer and a fallback did. On the
    # harness path this is the documentation provider failover recorded in
    # structured_answer.evidence_summary.documentation.fallback_used.
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # True only when canonical docs were insufficient AND an internal corpus layer
    # supplied the answer (see DocGapEvent). Only the retrieval ladder could observe
    # that; the harness runtime computes no equivalent signal, so it stays false
    # there rather than carrying a guess.
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
    # Validated snapshot of what the client was viewing when it sent this turn
    # (schemas.PageScope). Client-supplied and therefore untrusted: it is stored for
    # prompt disambiguation and replay only, and is never read back as an
    # authorization input or a retrieval filter.
    page_scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
