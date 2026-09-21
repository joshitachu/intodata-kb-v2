from __future__ import annotations

import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class ConversationLane(StrEnum):
    """Which reasoning runtime owns a conversation's turns.

    `StrEnum` rather than loose strings because these values are written to the
    database, compared in SQL filters, and returned on the wire; the lane was
    previously encoded as a `[labs] ` prefix on the user's own title, which broke
    the moment a user typed that prefix themselves, could not be indexed, and
    forced string surgery into every read path.

    The member values ARE the stored and published values, so renaming one is a
    breaking change for existing rows and for clients alike.
    """

    #: The production LangGraph harness (`/api/v1/conversations`).
    V1 = "v1"
    #: The OpenCode lane (`/api/v1/labs/*`).
    V2 = "v2"


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
    # Which reasoning runtime answers this thread's turns (`ConversationLane`).
    #
    # Orthogonal to the two fields above and deliberately not merged with them:
    # `origin_surface` records the UI surface a thread was born on and
    # `visible_in_chat` whether the user wants it listed, while the lane records
    # who answers. A V2 thread is an ordinary chat-surface thread that happens to
    # be answered by OpenCode.
    #
    # Fixed for the life of the thread, and no route updates it. A V2 thread owns
    # an OpenCode session holding its own history (`opencode_session_id` below)
    # and a V1 thread owns LangGraph conversation state; switching lanes mid
    # thread would hand the other runtime a conversation it has never seen, and
    # the user would get a confident answer built on none of what was said.
    #
    # Indexed because the unified conversation list can be narrowed to one lane.
    # Two distinct values make the index worth little on its own today; it is
    # cheap, and the alternative -- adding it later, under load, on the table the
    # sidebar reads on every page load -- is not.
    lane: Mapped[str] = mapped_column(
        String(16),
        default=ConversationLane.V1,
        server_default=ConversationLane.V1.value,
        index=True,
        nullable=False,
    )
    # The OpenCode session this conversation's labs turns run in, when it has
    # one. NULL for every conversation on the production `/chat` path, which has
    # no external reasoning session at all.
    #
    # Persisted because the id is the conversation's history on the OpenCode
    # side and nothing else can reconstruct it. The labs runtime used to hold
    # the binding only in process, so any restart -- and `uvicorn --reload` does
    # 39 in a working session -- made the next turn open a SECOND top-level
    # session for the same conversation. Verified on two live conversations,
    # each left with two sessions carrying `parentID=None`: the thread was split
    # and the model silently lost everything said before the restart.
    #
    # Deliberately the session id and nothing else. The MCP bearer token for the
    # same conversation stays out of the database: it is a credential, it is
    # re-mintable by design, and it belongs in memory and in the workspace's
    # `opencode.json`, which only the backend writes.
    opencode_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
