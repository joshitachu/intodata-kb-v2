"""Database models for template management system.

Tables: templates, template_versions, template_profiles, profile_assignments.
"""

from __future__ import annotations

import datetime
import hashlib

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mcp_server.db.models import Base


class Template(Base):
    """A template slot (e.g. 'assessment/scoring_rubric')."""

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    versions: Mapped[list[TemplateVersion]] = relationship(
        back_populates="template", passive_deletes=True, order_by="TemplateVersion.version_number.desc()"
    )


class TemplateVersion(Base):
    """An immutable version of a template's content."""

    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_template_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    template: Mapped[Template] = relationship(back_populates="versions")

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class TemplateProfile(Base):
    """A named set of template version assignments (the 'swap' unit)."""

    __tablename__ = "template_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), default="global", nullable=False)  # global | customer | project
    scope_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    assignments: Mapped[list[ProfileAssignment]] = relationship(
        back_populates="profile", passive_deletes=True
    )


class ProfileAssignment(Base):
    """Maps a template to a specific version within a profile."""

    __tablename__ = "profile_assignments"
    __table_args__ = (
        UniqueConstraint("profile_id", "template_id", name="uq_profile_template"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("template_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    profile: Mapped[TemplateProfile] = relationship(back_populates="assignments")
    template: Mapped[Template] = relationship()
    version: Mapped[TemplateVersion] = relationship()
