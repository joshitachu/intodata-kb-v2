"""Add Azure DevOps organization connections.

Azure DevOps repositories are cloned with an organization-level Personal Access
Token, stored Fernet-encrypted in ``azure_devops_connections`` and referenced from
``projects.azure_devops_connection_id``. ``projects.connection_kind`` is widened
from VARCHAR(10) so it can hold the new ``azure_devops`` value.

MUST BE IDEMPOTENT. The mcp-server container boots with
``create_all`` -> ``alembic upgrade head`` -> serve (see docker-compose.yml), so by
the time alembic runs, ``create_all`` has ALREADY created every table declared in
``db/models.py`` -- including this one. A plain ``op.create_table`` therefore dies
with DuplicateTableError, which leaves ``kb_alembic_version`` stranded on the
previous revision and blocks every later revision from ever applying. Any future
revision that creates a table or adds a column present in the models must guard the
same way.

Revision ID: 029_add_azure_devops_connections
Revises: 028_add_plugin_pack_ownership
Create Date: 2026-08-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029_add_azure_devops_connections"
down_revision: Union[str, None] = "028_add_plugin_pack_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "azure_devops_connections"
_INDEX = "ix_azure_devops_connections_owner_user_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("organization_url", sa.String(500), nullable=False),
            sa.Column("organization_name", sa.String(255), nullable=False),
            sa.Column("account_name", sa.String(255), nullable=True),
            sa.Column("pat_enc", sa.Text(), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "owner_user_id", "organization_url", name="uq_azure_devops_connection_owner_org"
            ),
        )

    if _INDEX not in {ix["name"] for ix in inspector.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, ["owner_user_id"])

    project_columns = {c["name"] for c in inspector.get_columns("projects")}
    if "azure_devops_connection_id" not in project_columns:
        op.add_column(
            "projects",
            sa.Column("azure_devops_connection_id", sa.String(36), nullable=True),
        )

    # SQLite ignores VARCHAR lengths, so the widening only matters on Postgres.
    # ALTER ... TYPE to the same type is already a no-op, so this needs no guard.
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "projects",
            "connection_kind",
            existing_type=sa.String(10),
            type_=sa.String(32),
            existing_nullable=False,
            existing_server_default="pat",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE projects SET connection_kind = 'pat' WHERE length(connection_kind) > 10"
        )
        op.alter_column(
            "projects",
            "connection_kind",
            existing_type=sa.String(32),
            type_=sa.String(10),
            existing_nullable=False,
            existing_server_default="pat",
        )
    op.drop_column("projects", "azure_devops_connection_id")
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
