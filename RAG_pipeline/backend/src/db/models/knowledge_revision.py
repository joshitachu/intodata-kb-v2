"""Durable RAG revision state used to keep prepared vectors invisible."""

from __future__ import annotations

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RagKnowledgeRevision(Base):
    """One immutable prepared/active revision membership for a project."""

    __tablename__ = "rag_knowledge_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "knowledge_revision_id", name="uq_rag_knowledge_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    knowledge_revision_id: Mapped[str] = mapped_column(String(255), nullable=False)
    base_knowledge_revision_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="prepared")
    membership_hashes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    deleted_logical_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    aliases: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ActiveRagKnowledgeRevision(Base):
    """The single revision visible to normal retrieval for each project."""

    __tablename__ = "active_rag_knowledge_revisions"

    project_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    knowledge_revision_id: Mapped[str] = mapped_column(String(255), nullable=False)
