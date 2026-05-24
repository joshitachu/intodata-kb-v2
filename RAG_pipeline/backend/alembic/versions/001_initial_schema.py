"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
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

    # Roles (groups for RBAC)
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("description", sa.String(500), nullable=True),
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

    # User-Role M2M
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Documents
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(500), nullable=False, index=True),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(500), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("department", sa.String(255), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "uploaded_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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

    # Query logs (audit)
    op.create_table(
        "query_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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

    # Ingestion jobs
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending", index=True
        ),
        sa.Column("chunks_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
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

    # Seed default admin user and role
    op.execute(
        "INSERT INTO roles (id, name, description) VALUES (1, 'admin', 'Administrator') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO users (id, username, email) VALUES (1, 'admin', 'admin@localhost') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (1, 1) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
    op.drop_table("query_logs")
    op.drop_table("documents")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
