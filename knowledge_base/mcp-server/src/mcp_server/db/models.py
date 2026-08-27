"""Database models for MCP server.

Uses the same PostgreSQL database as the RAG pipeline (intodata_kb).
Tables: projects, scan_runs.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
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
    # How this project's git credential is resolved. connection_kind:
    #   "pat"          -> github_token_enc (legacy/fallback, encrypted PAT on the project)
    #   "app"          -> github_installation_id, a non-secret reference used to mint
    #                     short-lived GitHub App tokens on demand
    #   "azure_devops" -> azure_devops_connection_id, an organization-level PAT held
    #                     once in azure_devops_connections instead of per project
    connection_kind: Mapped[str] = mapped_column(String(32), default="pat", server_default="pat", nullable=False)
    github_installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_account_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    azure_devops_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
    repo_sources: Mapped[list[ProjectRepoSource]] = relationship(
        back_populates="project", passive_deletes=True
    )


class ProjectRepoSource(Base):
    """One member repository of a multi-repo project.

    A single-repo project keeps using ``Project.repo_url`` /
    ``Project.last_ingested_commit_sha``; those columns cannot express N repos
    and are left untouched. When a project has rows here, these are the truth.

    Two identities matter and are deliberately separate:

    * ``repo_identity`` -- the provider's stable name for the repo
      (``owner/name`` on GitHub, ``org/project/repo`` on Azure DevOps). Indexed
      because it is the join key when a push event arrives and must resolve to
      exactly one repo's work.
    * ``subproject_name`` -- the origin-marker stem used for the clone directory
      and therefore the partition name, doc output subdirectory, and fingerprint
      key. Unique per project because two members resolving to one partition
      would silently merge their documentation.

    ``last_ingested_commit_sha`` is the per-repo incremental state: a push whose
    SHA already matches is a no-op, and only a repo whose SHA moved is pulled.
    """

    __tablename__ = "project_repo_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "subproject_name", name="uq_repo_source_subproject"),
        UniqueConstraint("project_id", "repo_identity", name="uq_repo_source_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # github | azure_devops
    repo_identity: Mapped[str] = mapped_column(String(500), index=True, nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    clone_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # The ref actually ingested. NULL means "whatever the clone resolved to",
    # which is how every pre-multi-repo project behaved.
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subproject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    local_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Credential references, mirroring Project.connection_kind semantics. Never
    # a secret: the PAT lives once in azure_devops_connections, and App tokens
    # are minted on demand from the installation id.
    github_installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    azure_devops_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    github_webhook_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_ingested_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_ingested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="repo_sources")


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


class AzureDevOpsConnection(Base):
    """An Azure DevOps organization connected with a Personal Access Token.

    Azure DevOps has no GitHub-App equivalent, so a PAT is the only credential.
    Holding it once per organization (rather than once per repository, the way the
    legacy ``projects.github_token_enc`` column does) means the user pastes a token
    a single time, and every project cloned from that organization resolves its
    credential through this row.

    The token is Fernet-encrypted with ``MCP_TOKEN_ENCRYPTION_KEY`` and never
    leaves the server. ``owner_user_id`` references the RAG users table, which MCP
    accesses via raw SQL, so it is a plain indexed column (no FK) — same as
    :class:`GithubAppInstallation`.
    """

    __tablename__ = "azure_devops_connections"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "organization_url", name="uq_azure_devops_connection_owner_org"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_url: Mapped[str] = mapped_column(String(500), nullable=False)
    organization_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pat_enc: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    last_verified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # Pack audit record (docs/one-pack-program.md section 9, "Substitute
    # controls for dropping Git on user packs"). Mandatory GitHub PR review is
    # dropped for user-authored packs, so every client deliverable must be
    # bindable to the exact pack version that produced it. All nullable: runs
    # with no active pack (the default handover contract) leave them NULL.
    # ``pack_activated_by`` is the actor identity string supplied by the RAG
    # side; this database has no ``users`` table, so it is deliberately not a
    # foreign key.
    pack_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pack_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pack_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pack_activated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pack_activated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="generate_runs")


class ConnectorInstance(Base):
    """Workspace-owned connector configuration; credentials are ciphertext only."""

    __tablename__ = "connector_instances"
    __table_args__ = (
        UniqueConstraint("provider_id", "account_key", name="uq_connector_instance_account"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalized identity of the account/domain this instance points at, so the
    # same cluster or tenant cannot be registered twice under two display names.
    # NULL where the provider's manifest declares no account field -- NULLs stay
    # distinct under the unique index, which is the behaviour wanted here.
    account_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ownership_scope: Mapped[str] = mapped_column(String(32), default="workspace", nullable=False)
    safe_config: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict, nullable=False)
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    kibana_credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_connected", nullable=False)
    last_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_version: Mapped[str] = mapped_column(String(32), default="1", nullable=False)
    redaction_policy_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


class ConnectorSource(Base):
    """Approved provider target.  The locator is encrypted separately from profile metadata."""

    __tablename__ = "connector_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label: Mapped[str] = mapped_column(String(500), nullable=False)
    locator_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    field_profile: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict, nullable=False)
    discovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovery_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProjectConnectorBindingModel(Base):
    """Project ceiling for source/capability access; versioned for immutable run snapshots."""

    __tablename__ = "project_connector_bindings"
    __table_args__ = (UniqueConstraint("project_id", "connector_instance_id", name="uq_project_connector_binding"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connector_instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connector_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_ids: Mapped[list] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=list, nullable=False)
    capability_ids: Mapped[list] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=list, nullable=False)
    budgets: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict, nullable=False)
    max_time_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


class GenerationConnectorSnapshot(Base):
    """Immutable effective binding and grant metadata for one generation run."""

    __tablename__ = "generation_connector_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generate_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("generate_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    initiating_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connector_instance_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    binding_id: Mapped[str] = mapped_column(String(36), nullable=False)
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    grant_nonce_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    grant_issued_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grant_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grant_revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)


class ConnectorInvocation(Base):
    """Sanitized immutable receipt; never stores a provider response body."""

    __tablename__ = "connector_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    generation_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generation_connector_snapshots.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connector_instance_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query_summary: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    redaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    truncation: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=dict, nullable=False)
    limitations: Mapped[list] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), default=list, nullable=False)
    result_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    denial_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


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

    Two lanes, one format:

    * ``owner_user_id IS NULL`` -- a SHARED pack, cloned from the configured
      pack repository. Visible to everyone, immutable in-app, changed only by
      merging a pull request upstream.
    * ``owner_user_id`` set -- a PERSONAL pack authored in Nexus by that user.
      Visible and usable only to its owner, freely editable, and publishable to
      the shared repository as a branch + pull request.
    """

    __tablename__ = "plugin_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Uniqueness is enforced by two indexes (migration 028): globally for
    # shared packs, per-owner for personal ones. Not `unique=True` here.
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: NULL = shared (from the pack repository); set = private to that user.
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
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
