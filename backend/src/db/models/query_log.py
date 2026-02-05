from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_text: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[dict | None] = mapped_column(JSON)  # list of source citations
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # Foreign keys
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    user: Mapped["User | None"] = relationship(back_populates="query_logs")
