"""superadmin LLM answer-model standard: system_config.llm_provider/llm_model

Adds the superadmin-curated generation-model override to the single-row
system_config. NULL means "use the deployment env defaults" (backward
compatible). Only the answer LLM is configurable — embeddings/rerank stay
env-managed.

Revision ID: 037_rbac
Revises: 036_rbac
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037_rbac"
down_revision: Union[str, None] = "036_rbac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_config", sa.Column("llm_provider", sa.String(50), nullable=True)
    )
    op.add_column(
        "system_config", sa.Column("llm_model", sa.String(200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("system_config", "llm_model")
    op.drop_column("system_config", "llm_provider")
