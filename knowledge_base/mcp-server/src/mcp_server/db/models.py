"""Database models for MCP server.

Uses the same PostgreSQL database as the RAG pipeline (intodata_kb).
Tables: projects, scan_runs.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
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
    doc_generation_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    last_ingested_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    auto_reingest_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_reingest_last_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    auto_reingest_error: Mapped[str | None] = mapped_column(Text)
    git_update_summary: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    github_token_enc: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_webhook_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # GitHub App connection (repo access via installation instead of a stored PAT).
    # connection_kind: "pat" (default, legacy/fallback) | "app". github_installation_id
    # is a non-secret reference used to mint short-lived tokens on demand.
    connection_kind: Mapped[str] = mapped_column(String(10), default="pat", server_default="pat", nullable=False)
    github_installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_account_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instruction_pack_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plugin_pack_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plugin_packs.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    scan_runs: Mapped[list[ScanRun]] = relationship(
        back_populates="project", passive_deletes=True
    )
    generate_runs: Mapped[list[GenerateRun]] = relationship(
        back_populates="project", passive_deletes=True
    )
    group_scan_hashes: Mapped[list[ProjectGroupScanHash]] = relationship(
        back_populates="project", passive_deletes=True
    )
    doc_rag_hashes: Mapped[list[ProjectDocRagHash]] = relationship(
        back_populates="project", passive_deletes=True
    )


class GithubAppInstallation(Base):
    """Ownership binding for a GitHub App installation.

    Records which user connected an installation (verified at callback time via the
    user's own GET /user/installations). Multi-tenant deployments authorize the
    repos / add-repo / callback endpoints against this so a user cannot use another
    tenant's (enumerable) installation id. owner_user_id references the RAG users
    table, which MCP accesses via raw SQL, so it is a plain indexed column (no FK).
    """

    __tablename__ = "github_app_installations"

    installation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    account_login: Mapped[str | None] = mapped_column(String(255), nullable=True)


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
    )  # running | completed | completed_warnings | failed
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
    model_name: Mapped[str | None] = mapped_column(String(100))
    write_model_name: Mapped[str | None] = mapped_column(String(100))
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    log_file: Mapped[str | None] = mapped_column(String(500))

    # Auto-comparison verdict (US-104)
    auto_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "PASS" | "FAIL"
    auto_baseline_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set for single-phase reruns
    run_verdict: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gate_results: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="generate_runs")


class ProjectGroupScanHash(Base):
    """Per-group scan content hash baseline for fingerprint-based dirty detection."""

    __tablename__ = "project_group_scan_hashes"

    project_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    group_name: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    scan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scanned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="group_scan_hashes")


class ProjectDocRagHash(Base):
    """Per-doc SHA-256 content hash baseline for vector-store push skip (Slice 2)."""

    __tablename__ = "project_doc_rag_hashes"

    project_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    doc_filename: Mapped[str] = mapped_column(String(500), primary_key=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex
    pushed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="doc_rag_hashes")


class PluginPack(Base):
    """Registry of plugin packs available for doc generation.

    A pack directory must contain a plugin.yaml manifest.  When installed from
    a GitHub URL the local clone path is stored in `path` and the HEAD SHA in
    `last_commit_sha` so the poller/webhook can detect updates.
    """

    __tablename__ = "plugin_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    manifest_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    repo_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InstanceSetting(Base):
    """Per-instance model override (one row per key).

    Mirrors the RAG-side model. The `instance_settings` table is created by the
    RAG backend's alembic migration (042_instance_settings); both processes
    read and write the same row to keep their effective model defaults in
    sync. Defined here so the MCP server can use SQLAlchemy ORM queries
    instead of raw SQL.
    """

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
