"""Add project_repo_sources: N member repositories per project.

A project could previously hold exactly one repository -- ``projects.repo_url``
plus one ``projects.last_ingested_commit_sha``. Multi-repo projects need one row
per member so that (a) a push event resolves to exactly one repo, and (b) each
repo carries its own last-ingested SHA, which is what makes re-ingestion
incremental instead of project-wide.

The legacy single-repo columns are NOT touched or migrated. A project with no
rows here keeps behaving exactly as before; a project with rows treats them as
the truth. That keeps this revision additive and reversible.

MUST BE IDEMPOTENT. The mcp-server container boots with ``create_all`` ->
``alembic upgrade head`` -> serve (see docker-compose.yml), so by the time
alembic runs, ``create_all`` has ALREADY created any table declared in
``db/models.py``. A plain ``op.create_table`` therefore dies, stranding
``kb_alembic_version`` on the previous revision and blocking every later one.
Each object is created only when absent.

Revision ID: 031_add_project_repo_sources
Revises: 030_add_connector_account_key
Create Date: 2026-08-27

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "031_add_project_repo_sources"
down_revision = "030_add_connector_account_key"
branch_labels = None
depends_on = None

_TABLE = "project_repo_sources"
_IX_PROJECT = "ix_project_repo_sources_project_id"
_IX_IDENTITY = "ix_project_repo_sources_repo_identity"
_UQ_SUBPROJECT = "uq_repo_source_subproject"
_UQ_IDENTITY = "uq_repo_source_identity"


def _existing_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    names = {i["name"] for i in inspector.get_indexes(_TABLE)}
    names |= {c["name"] for c in inspector.get_unique_constraints(_TABLE)}
    return {name for name in names if name}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(100),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("repo_identity", sa.String(500), nullable=False),
            sa.Column("repo_name", sa.String(255), nullable=False),
            sa.Column("clone_url", sa.String(500), nullable=False),
            sa.Column("branch", sa.String(255), nullable=True),
            sa.Column("subproject_name", sa.String(255), nullable=False),
            sa.Column("local_path", sa.String(1000), nullable=False),
            sa.Column("github_installation_id", sa.Integer(), nullable=True),
            sa.Column("azure_devops_connection_id", sa.String(36), nullable=True),
            sa.Column("github_webhook_id", sa.Integer(), nullable=True),
            sa.Column("last_ingested_commit_sha", sa.String(40), nullable=True),
            sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            # Declared INLINE, not via a later create_unique_constraint: SQLite
            # has no ALTER for constraints, so adding them after the fact fails
            # outright there. Two members resolving to one partition would
            # silently merge their documentation; two rows for one repo would
            # double-ingest it.
            sa.UniqueConstraint("project_id", "subproject_name", name=_UQ_SUBPROJECT),
            sa.UniqueConstraint("project_id", "repo_identity", name=_UQ_IDENTITY),
        )

    present = _existing_names(bind)
    if _IX_PROJECT not in present:
        op.create_index(_IX_PROJECT, _TABLE, ["project_id"])
    if _IX_IDENTITY not in present:
        op.create_index(_IX_IDENTITY, _TABLE, ["repo_identity"])
    # Only reachable when the table pre-existed WITHOUT its constraints -- a
    # hand-made table, or a future model edit. create_all declares both inline,
    # so the normal container boot path finds them already present. Skipped on
    # SQLite, which cannot ALTER them in; production is PostgreSQL.
    if bind.dialect.name != "sqlite":
        if _UQ_SUBPROJECT not in present:
            op.create_unique_constraint(_UQ_SUBPROJECT, _TABLE, ["project_id", "subproject_name"])
        if _UQ_IDENTITY not in present:
            op.create_unique_constraint(_UQ_IDENTITY, _TABLE, ["project_id", "repo_identity"])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    op.drop_table(_TABLE)
