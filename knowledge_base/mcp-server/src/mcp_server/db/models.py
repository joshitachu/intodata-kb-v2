"""Database models for MCP server.

Uses the same PostgreSQL database as the RAG pipeline (intodata_kb).
Tables: projects, scan_runs.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    generation_experiments: Mapped[list[GenerationExperiment]] = relationship(
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
    triggered_by: Mapped[str] = mapped_column(String(40), nullable=False)  # mcp | api | webhook | mission_control
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
    # Trigger source only (api | mcp | webhook | mission_control | composite |
    # manual_full_rebuild). Composite sub-project runs previously encoded the
    # partition name here ("composite:{name}"), overflowing the column; the
    # partition now lives in its own field.
    triggered_by: Mapped[str] = mapped_column(String(40), nullable=False)
    # Composite orchestration: the sub-project partition this child run covers.
    partition_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
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


class GenerationExperiment(Base):
    """Additive, non-publishing agentic documentation experiment.

    This row is deliberately separate from ``GenerateRun``: experiment candidates
    never enter the legacy staging, atomic-swap, or RAG publication lifecycle.
    """

    __tablename__ = "generation_experiments"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_generation_experiments_project_idempotency",
        ),
        Index(
            "ix_generation_experiments_project_created",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    baseline_run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("generate_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default="queued",
        nullable=False,
        index=True,
    )
    comparison_status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
    )
    candidate_verdict: Mapped[str] = mapped_column(
        String(32),
        default="not_evaluated",
        nullable=False,
    )
    runtime_id: Mapped[str] = mapped_column(String(100), default="fake", nullable=False)
    runtime_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    publishes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    baseline_artifact_root: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    candidate_artifact_root: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    baseline_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "generation_source_snapshots.id",
            name="fk_generation_experiments_source_snapshot",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    recipe_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "generation_recipe_snapshots.id",
            name="fk_generation_experiments_recipe_snapshot",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    wall_duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    critical_path_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settings_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    metrics_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    comparison_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="generation_experiments")
    baseline_run: Mapped[GenerateRun | None] = relationship()
    artifacts: Mapped[list[GenerationExperimentArtifact]] = relationship(
        back_populates="experiment",
        passive_deletes=True,
    )
    source_snapshot: Mapped[GenerationSourceSnapshot | None] = relationship(
        foreign_keys=[source_snapshot_id],
        post_update=True,
    )
    recipe_snapshot: Mapped[GenerationRecipeSnapshot | None] = relationship(
        foreign_keys=[recipe_snapshot_id],
        post_update=True,
    )


class GenerationExperimentArtifact(Base):
    """Immutable artifact index; document bytes stay on the filesystem."""

    __tablename__ = "generation_experiment_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "branch",
            "kind",
            "logical_name",
            "sha256",
            name="uq_generation_experiment_artifact_identity",
        ),
        UniqueConstraint(
            "experiment_id",
            "relative_path",
            name="uq_generation_experiment_artifact_path",
        ),
        Index(
            "ix_generation_experiment_artifacts_lookup",
            "experiment_id",
            "branch",
            "kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Candidate artifacts may be attributable to a task/attempt, but historical
    # baseline artifacts deliberately remain experiment-scoped.  These nullable
    # links make provenance additive without changing the legacy publication path.
    task_run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("generation_task_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_task_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    branch: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    logical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_metadata: Mapped[dict | None] = mapped_column(
        "metadata_json",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    experiment: Mapped[GenerationExperiment] = relationship(back_populates="artifacts")
    task_run: Mapped[GenerationTaskRun | None] = relationship(
        back_populates="artifacts",
        foreign_keys=[task_run_id],
    )
    task_attempt: Mapped[GenerationTaskAttempt | None] = relationship(
        back_populates="artifacts",
        foreign_keys=[attempt_id],
    )


class GenerationSourceSnapshot(Base):
    """Immutable, redaction-aware aggregate of approved experiment inputs."""

    __tablename__ = "generation_source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            name="uq_generation_source_snapshots_experiment",
        ),
        Index(
            "ix_generation_source_snapshots_security",
            "security_state",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    security_state: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    snapshot_metadata: Mapped[dict | None] = mapped_column(
        "metadata_json",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    owner_experiment: Mapped[GenerationExperiment] = relationship(
        foreign_keys=[experiment_id],
    )
    items: Mapped[list[GenerationSourceSnapshotItem]] = relationship(
        back_populates="snapshot",
        passive_deletes=True,
    )


class GenerationSourceSnapshotItem(Base):
    """Open-taxonomy item indexed beneath a frozen source snapshot."""

    __tablename__ = "generation_source_snapshot_items"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "logical_identity",
            name="uq_generation_source_snapshot_item_identity",
        ),
        Index(
            "ix_generation_source_snapshot_items_role_kind",
            "snapshot_id",
            "source_role",
            "source_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_source_snapshots.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    source_descriptor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    logical_identity: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(200), nullable=False)
    source_role: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities_json: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extracted_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(100), nullable=False)
    redaction_state: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(200), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    item_metadata: Mapped[dict | None] = mapped_column(
        "metadata_json",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    snapshot: Mapped[GenerationSourceSnapshot] = relationship(back_populates="items")


class GenerationRecipeSnapshot(Base):
    """Immutable resolved recipe, prompt, scope, and compiled-graph receipt."""

    __tablename__ = "generation_recipe_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            name="uq_generation_recipe_snapshots_experiment",
        ),
        Index(
            "ix_generation_recipe_snapshots_identity",
            "recipe_id",
            "recipe_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipe_id: Mapped[str] = mapped_column(String(200), nullable=False)
    recipe_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    certification_id: Mapped[str | None] = mapped_column(String(200))
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_requirements_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_scope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_prompts_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_contracts_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    recipe_metadata: Mapped[dict | None] = mapped_column(
        "metadata_json",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    owner_experiment: Mapped[GenerationExperiment] = relationship(
        foreign_keys=[experiment_id],
    )
    task_runs: Mapped[list[GenerationTaskRun]] = relationship(
        back_populates="recipe_snapshot",
        passive_deletes=True,
    )


class GenerationTaskRun(Base):
    """Durable execution identity for one generic compiled task node."""

    __tablename__ = "generation_task_runs"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "node_id",
            name="uq_generation_task_runs_experiment_node",
        ),
        Index(
            "ix_generation_task_runs_scheduler",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("generation_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipe_snapshot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_recipe_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(500), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    dependencies_json: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    dependency_output_refs_json: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    output_ids_json: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_id: Mapped[str | None] = mapped_column(String(100))
    provider_id: Mapped[str | None] = mapped_column(String(100))
    model_id: Mapped[str | None] = mapped_column(String(200))
    runtime_session_id: Mapped[str | None] = mapped_column(String(500))
    runtime_message_id: Mapped[str | None] = mapped_column(String(500))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    wall_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    provider_latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_cost_usd: Mapped[str | None] = mapped_column(String(50))
    request_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    result_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    metrics_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    recipe_snapshot: Mapped[GenerationRecipeSnapshot] = relationship(
        back_populates="task_runs",
    )
    attempts: Mapped[list[GenerationTaskAttempt]] = relationship(
        back_populates="task_run",
        passive_deletes=True,
        order_by="GenerationTaskAttempt.attempt_number",
    )
    events: Mapped[list[GenerationTaskEvent]] = relationship(
        back_populates="task_run",
        passive_deletes=True,
        order_by="GenerationTaskEvent.sequence_number",
    )
    artifacts: Mapped[list[GenerationExperimentArtifact]] = relationship(
        back_populates="task_run",
        foreign_keys="GenerationExperimentArtifact.task_run_id",
    )


class GenerationTaskAttempt(Base):
    """Append-only attempt record for one leased compiled task.

    A row is inserted when a worker acquires a lease and only receives terminal
    outcome fields afterwards.  Retrying always creates the next attempt number;
    it never replaces prior execution evidence.
    """

    __tablename__ = "generation_task_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_run_id",
            "attempt_number",
            name="uq_generation_task_attempts_task_number",
        ),
        UniqueConstraint(
            "execution_idempotency_key",
            name="uq_generation_task_attempts_execution_key",
        ),
        Index(
            "ix_generation_task_attempts_task_status",
            "task_run_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    wall_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    provider_latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_cost_usd: Mapped[str | None] = mapped_column(String(50))
    runtime_session_id: Mapped[str | None] = mapped_column(String(500))
    runtime_message_id: Mapped[str | None] = mapped_column(String(500))
    result_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    metrics_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    task_run: Mapped[GenerationTaskRun] = relationship(back_populates="attempts")
    events: Mapped[list[GenerationTaskEvent]] = relationship(
        back_populates="attempt",
        passive_deletes=True,
        order_by="GenerationTaskEvent.sequence_number",
    )
    artifacts: Mapped[list[GenerationExperimentArtifact]] = relationship(
        back_populates="task_attempt",
        foreign_keys="GenerationExperimentArtifact.attempt_id",
    )


class GenerationTaskEvent(Base):
    """Idempotent, ordered scheduler/runtime event ledger for one task."""

    __tablename__ = "generation_task_events"
    __table_args__ = (
        UniqueConstraint(
            "task_run_id",
            "sequence_number",
            name="uq_generation_task_events_task_sequence",
        ),
        UniqueConstraint(
            "task_run_id",
            "idempotency_key",
            name="uq_generation_task_events_task_idempotency",
        ),
        Index(
            "ix_generation_task_events_task_occurred",
            "task_run_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_task_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task_run: Mapped[GenerationTaskRun] = relationship(back_populates="events")
    attempt: Mapped[GenerationTaskAttempt | None] = relationship(back_populates="events")


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
    # Governance metadata is intentionally separate from mutable manifest data.
    # ``content_sha256`` pins the exact installed tree that was certified.
    origin: Mapped[str] = mapped_column(String(32), default="legacy", nullable=False)
    certification_status: Mapped[str] = mapped_column(
        String(32), default="legacy_untrusted", nullable=False
    )
    certification_provenance: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
