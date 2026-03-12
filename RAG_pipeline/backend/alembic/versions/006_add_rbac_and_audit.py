"""add permissions, role_permissions, project_assignments, audit_logs tables

Revision ID: 006
Revises: 005
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- permissions table ---
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codename", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- role_permissions M2M ---
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )

    # --- project_assignments ---
    op.create_table(
        "project_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )
    op.create_index("ix_project_assignments_user_id", "project_assignments", ["user_id"])
    op.create_index("ix_project_assignments_project_id", "project_assignments", ["project_id"])

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # --- Seed permissions ---
    permissions_table = sa.table(
        "permissions",
        sa.column("id", sa.Integer),
        sa.column("codename", sa.String),
        sa.column("description", sa.String),
    )
    permissions = [
        {"id": 1, "codename": "manage_users", "description": "Create, update, deactivate users"},
        {"id": 2, "codename": "manage_roles", "description": "Modify roles and their permissions"},
        {"id": 3, "codename": "assign_projects", "description": "Assign projects to users"},
        {"id": 4, "codename": "view_all_projects", "description": "See all projects regardless of assignment"},
        {"id": 5, "codename": "manage_projects", "description": "Trigger scans, generate docs, push to RAG"},
        {"id": 6, "codename": "view_assigned_projects", "description": "See only projects assigned to the user"},
        {"id": 7, "codename": "query_rag", "description": "Run RAG knowledge base queries"},
        {"id": 8, "codename": "ingest_documents", "description": "Upload documents to the knowledge base"},
        {"id": 9, "codename": "view_audit_logs", "description": "View the audit trail"},
    ]
    op.bulk_insert(permissions_table, permissions)

    # --- Seed role_permissions ---
    role_permissions_table = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Integer),
        sa.column("permission_id", sa.Integer),
    )

    # Create manager and developer roles
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(roles_table, [
        {"id": 2, "name": "manager", "description": "Manager: full project access and user assignment"},
        {"id": 3, "name": "developer", "description": "Developer: view assigned projects and query RAG"},
    ])

    # Admin (role_id=1) gets all permissions
    admin_perms = [{"role_id": 1, "permission_id": pid} for pid in range(1, 10)]
    # Manager gets all except view_assigned_projects (id=6)
    manager_perms = [{"role_id": 2, "permission_id": pid} for pid in [1, 2, 3, 4, 5, 7, 8, 9]]
    # Developer gets view_assigned_projects, query_rag, ingest_documents
    developer_perms = [{"role_id": 3, "permission_id": pid} for pid in [6, 7, 8]]

    op.bulk_insert(role_permissions_table, admin_perms + manager_perms + developer_perms)

    # Fix sequences after explicit ID inserts (use GREATEST to avoid 0 on empty tables)
    op.execute("SELECT setval('permissions_id_seq', GREATEST((SELECT COALESCE(MAX(id), 0) FROM permissions), 1))")
    op.execute("SELECT setval('roles_id_seq', GREATEST((SELECT COALESCE(MAX(id), 0) FROM roles), 1))")
    op.execute("SELECT setval('project_assignments_id_seq', GREATEST((SELECT COALESCE(MAX(id), 0) FROM project_assignments), 1))")


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("project_assignments")
    op.drop_table("role_permissions")
    op.drop_table("permissions")

    # Remove seeded roles (manager, developer)
    op.execute("DELETE FROM roles WHERE name IN ('manager', 'developer')")
