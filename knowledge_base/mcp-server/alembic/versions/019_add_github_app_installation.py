"""Add GitHub App installation columns to projects.

Repos can connect via a GitHub App installation instead of a stored PAT. This adds
the non-secret installation reference and the connection-kind discriminator.

Revision ID: 019_add_github_app_installation
Revises: 018_add_doc_rag_hashes
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019_add_github_app_installation"
down_revision: Union[str, None] = "018_add_doc_rag_hashes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "connection_kind",
            sa.String(10),
            server_default="pat",
            nullable=False,
        ),
    )
    op.add_column(
        "projects",
        sa.Column("github_installation_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("github_account_login", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "github_account_login")
    op.drop_column("projects", "github_installation_id")
    op.drop_column("projects", "connection_kind")
