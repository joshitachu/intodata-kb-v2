"""Add governed live connector persistence.

Revision ID: 027_live_connectors
Revises: 026_generate_run_pack_audit
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "027_live_connectors"
down_revision: Union[str, None] = "026_generate_run_pack_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return sa.JSON()


def upgrade() -> None:
    # The service historically calls ``Base.metadata.create_all`` before
    # Alembic.  Connector tables can therefore already exist on first rollout
    # even though this revision has not been recorded yet.  Adopt those exact
    # model-created tables and still apply the permission grants below.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "connector_instances" not in existing_tables:
        op.create_table(
            "connector_instances",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider_id", sa.String(100), nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("ownership_scope", sa.String(32), nullable=False, server_default="workspace"),
            sa.Column("safe_config", _json_type(), nullable=False),
            sa.Column("credential_type", sa.String(32), nullable=False),
            sa.Column("credential_ciphertext", sa.Text(), nullable=False),
            sa.Column("kibana_credential_ciphertext", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(32), nullable=False, server_default="not_connected"),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("health_code", sa.String(64), nullable=True),
            sa.Column("provider_version", sa.String(32), nullable=False, server_default="1"),
            sa.Column(
                "redaction_policy_version", sa.String(32), nullable=False, server_default="v1"
            ),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_connector_instances_provider_id", "connector_instances", ["provider_id"]
        )
    if "connector_sources" not in existing_tables:
        op.create_table(
            "connector_sources",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "connector_instance_id",
                sa.String(36),
                sa.ForeignKey("connector_instances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_type", sa.String(64), nullable=False),
            sa.Column("display_label", sa.String(500), nullable=False),
            sa.Column("locator_ciphertext", sa.Text(), nullable=False),
            sa.Column("field_profile", _json_type(), nullable=False),
            sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("discovery_digest", sa.String(64), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_connector_sources_connector_instance_id",
            "connector_sources",
            ["connector_instance_id"],
        )
    if "project_connector_bindings" not in existing_tables:
        op.create_table(
            "project_connector_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(100),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connector_instance_id",
                sa.String(36),
                sa.ForeignKey("connector_instances.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_ids", _json_type(), nullable=False),
            sa.Column("capability_ids", _json_type(), nullable=False),
            sa.Column("budgets", _json_type(), nullable=False),
            sa.Column("max_time_window_days", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "project_id", "connector_instance_id", name="uq_project_connector_binding"
            ),
        )
        op.create_index(
            "ix_project_connector_bindings_project_id", "project_connector_bindings", ["project_id"]
        )
    if "generation_connector_snapshots" not in existing_tables:
        op.create_table(
            "generation_connector_snapshots",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "generate_run_id",
                sa.Integer(),
                sa.ForeignKey("generate_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("project_id", sa.String(100), nullable=False),
            sa.Column("initiating_user_id", sa.Integer(), nullable=False),
            sa.Column("connector_instance_id", sa.String(36), nullable=False),
            sa.Column("binding_id", sa.String(36), nullable=False),
            sa.Column("binding_version", sa.Integer(), nullable=False),
            sa.Column("snapshot", _json_type(), nullable=False),
            sa.Column("grant_nonce_hash", sa.String(64), nullable=True, unique=True),
            sa.Column("grant_issued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("grant_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("grant_revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("digest", sa.String(71), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_generation_connector_snapshots_generate_run_id",
            "generation_connector_snapshots",
            ["generate_run_id"],
        )
    if "connector_invocations" not in existing_tables:
        op.create_table(
            "connector_invocations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("evidence_id", sa.String(64), nullable=True, unique=True),
            sa.Column(
                "generation_snapshot_id",
                sa.String(36),
                sa.ForeignKey("generation_connector_snapshots.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("connector_instance_id", sa.String(36), nullable=False),
            sa.Column("source_id", sa.String(36), nullable=False),
            sa.Column("capability_id", sa.String(128), nullable=False),
            sa.Column("query_summary", _json_type(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("redaction_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("truncation", _json_type(), nullable=False),
            sa.Column("limitations", _json_type(), nullable=False),
            sa.Column("result_digest", sa.String(71), nullable=True),
            sa.Column("denial_reason", sa.String(64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    # The user/role tables are shared with the RAG service.  Use codenames and
    # idempotent role grants so this additive migration works on existing DBs.
    op.execute(
        "INSERT INTO permissions (codename, description) VALUES "
        "('view_connectors', 'View safe connector metadata and health'), "
        "('manage_connectors', 'Create, test, rotate, disable, and delete connectors') "
        "ON CONFLICT (codename) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT roles.id, permissions.id FROM roles CROSS JOIN permissions "
        "WHERE roles.name = 'admin' AND permissions.codename IN ('view_connectors', 'manage_connectors') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE codename IN ('view_connectors', 'manage_connectors'))"
    )
    op.execute("DELETE FROM permissions WHERE codename IN ('view_connectors', 'manage_connectors')")
    op.drop_table("connector_invocations")
    op.drop_table("generation_connector_snapshots")
    op.drop_table("project_connector_bindings")
    op.drop_table("connector_sources")
    op.drop_table("connector_instances")
