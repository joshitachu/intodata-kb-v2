"""Database models for MCP server.

Uses the same PostgreSQL database as the RAG pipeline (intodata_kb).
Tables: projects, scan_runs.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base with automatic timestamps (matches RAG pipeline Base)."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String(500))
    local_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), default="talend")
    source_type: Mapped[str] = mapped_column(String(20), default="local")  # local | git | zip
    status: Mapped[str] = mapped_column(
        String(20), default="registered", index=True
    )  # registered | scanning | generating | ready | error
    last_scan_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_generate_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_rag_push_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    scan_result_path: Mapped[str | None] = mapped_column(String(1000))
    output_dir: Mapped[str | None] = mapped_column(String(1000))
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    # Relationships
    scan_runs: Mapped[list[ScanRun]] = relationship(
        back_populates="project", passive_deletes=True
    )
    generate_runs: Mapped[list[GenerateRun]] = relationship(
        back_populates="project", passive_deletes=True
    )


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False)  # mcp | api | webhook
    status: Mapped[str] = mapped_column(
        String(20), default="running", index=True
    )  # running | completed | failed
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    log_file: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    project: Mapped[Project] = relationship(back_populates="scan_runs")


class GenerateRun(Base):
    __tablename__ = "generate_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False)  # api | mcp
    status: Mapped[str] = mapped_column(
        String(20), default="running", index=True
    )  # running | completed | failed
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    # Usage tracking
    cost_usd: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    duration_api_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    num_turns: Mapped[int | None] = mapped_column(Integer)
    session_id: Mapped[str | None] = mapped_column(String(200))

    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    log_file: Mapped[str | None] = mapped_column(String(500))

    # Relationships
    project: Mapped[Project] = relationship(back_populates="generate_runs")
