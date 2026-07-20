"""Add Pack certification provenance and integrity metadata.

Revision ID: 021_plugin_pack_governance
Revises: 020_add_github_app_installations
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_plugin_pack_governance"
down_revision: Union[str, None] = "020_add_github_app_installations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plugin_packs", sa.Column("origin", sa.String(32), nullable=True))
    op.add_column("plugin_packs", sa.Column("certification_status", sa.String(32), nullable=True))
    op.add_column("plugin_packs", sa.Column("certification_provenance", sa.String(100), nullable=True))
    op.add_column("plugin_packs", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.execute("UPDATE plugin_packs SET origin = 'legacy' WHERE origin IS NULL")
    op.execute("UPDATE plugin_packs SET certification_status = 'legacy_untrusted' WHERE certification_status IS NULL")
    op.alter_column("plugin_packs", "origin", nullable=False, server_default="legacy")
    op.alter_column("plugin_packs", "certification_status", nullable=False, server_default="legacy_untrusted")


def downgrade() -> None:
    op.drop_column("plugin_packs", "content_sha256")
    op.drop_column("plugin_packs", "certification_provenance")
    op.drop_column("plugin_packs", "certification_status")
    op.drop_column("plugin_packs", "origin")
