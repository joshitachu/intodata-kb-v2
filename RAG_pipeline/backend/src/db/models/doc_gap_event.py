from __future__ import annotations

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class DocGapEvent(Base):
    """One retrieval-ladder fallback: internal analysis answered what final docs could not.

    Written only when rung 1 (canonical docs) was insufficient AND rung 2
    (internal layers) supplied answer-supporting material. Clean refusals are
    not gaps. Rows feed repeated-omission analysis: which questions keep
    descending past the final docs, per project.
    """

    __tablename__ = "doc_gap_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query_text: Mapped[str] = mapped_column(Text)
    detected_entities: Mapped[list | None] = mapped_column(_JSON, nullable=True)
    insufficiency_reason: Mapped[str] = mapped_column(String(50))
    primary_source_layer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    layers_used: Mapped[list | None] = mapped_column(_JSON, nullable=True)
    supporting_artifacts: Mapped[list | None] = mapped_column(_JSON, nullable=True)
    supporting_chunk_ids: Mapped[list | None] = mapped_column(_JSON, nullable=True)
