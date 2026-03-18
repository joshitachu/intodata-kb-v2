from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), index=True)
    file_type: Mapped[str] = mapped_column(String(20))  # pdf, docx, pptx, xlsx
    source: Mapped[str | None] = mapped_column(String(500))  # origin path/URL
    author: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255))
    chunk_count: Mapped[int] = mapped_column(default=0)
    project_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Foreign keys
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    uploaded_by_user: Mapped["User | None"] = relationship(back_populates="documents")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(
        back_populates="document", passive_deletes=True
    )
