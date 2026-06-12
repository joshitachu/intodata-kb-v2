from __future__ import annotations

from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class EntityAlias(Base):
    """Per-project alias lookup for entities extracted from generated docs.

    Populated from `_facts/entity_index.csv` and `_facts/usage_index.csv` by the
    MCP server after each generation push. Replaced wholesale per project — rows
    are a lookup aid for query-time entity matching, never a vector corpus.
    """

    __tablename__ = "entity_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)
    alias: Mapped[str] = mapped_column(String(500))
    alias_normalized: Mapped[str] = mapped_column(String(500))
    canonical_name: Mapped[str] = mapped_column(String(500))
    entity_type: Mapped[str] = mapped_column(String(100))
    unit_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_artifact: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        Index("ix_entity_aliases_project_alias", "project_id", "alias_normalized"),
    )
