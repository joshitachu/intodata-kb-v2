"""Permission checking utilities for MCP server endpoints.

Uses raw SQL against the shared PostgreSQL database to check permissions
without importing RAG backend models.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import text

from mcp_server.db.session import async_session_factory


async def require_project_access(request: Request, project_id: str) -> None:
    """Check that the authenticated user can access the given project.

    Access is granted if: superadmin, has view_all_projects permission
    (admin role), or has a project_assignment row for this project.
    """
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = int(payload.get("sub", 0))

    async with async_session_factory() as db:
        # Check superadmin
        row = await db.execute(
            text("SELECT is_superadmin FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user_row = row.fetchone()
        if user_row and user_row[0]:
            return

        # Check view_all_projects permission (admin role only)
        perm_result = await db.execute(text("""
            SELECT 1 FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = :uid AND p.codename = 'view_all_projects'
            LIMIT 1
        """), {"uid": user_id})
        if perm_result.fetchone():
            return

        # Check project assignment
        assign_result = await db.execute(text("""
            SELECT 1 FROM project_assignments
            WHERE user_id = :uid AND project_id = :pid
            LIMIT 1
        """), {"uid": user_id, "pid": project_id})
        if assign_result.fetchone():
            return

    raise HTTPException(status_code=403, detail="No access to this project")


async def require_project_management(request: Request, project_id: str) -> None:
    """Require project ownership (or superadmin) for project configuration changes."""
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = int(payload.get("sub", 0))
    async with async_session_factory() as db:
        user_result = await db.execute(
            text("SELECT is_superadmin FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user_row = user_result.fetchone()
        if user_row and user_row[0]:
            return

        owner_result = await db.execute(
            text("""
                SELECT 1 FROM project_assignments
                WHERE user_id = :uid AND project_id = :pid AND is_owner = 1
                LIMIT 1
            """),
            {"uid": user_id, "pid": project_id},
        )
        if owner_result.fetchone():
            return

    raise HTTPException(status_code=403, detail="Project management permission required")


async def require_pack_management(request: Request) -> None:
    """Require workspace-level permission to create reusable documentation Packs."""
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = int(payload.get("sub", 0))
    async with async_session_factory() as db:
        user_result = await db.execute(
            text("SELECT is_superadmin FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user_row = user_result.fetchone()
        if user_row and user_row[0]:
            return

        permission_result = await db.execute(text("""
            SELECT 1 FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = :uid AND p.codename = 'manage_projects'
            LIMIT 1
        """), {"uid": user_id})
        if permission_result.fetchone():
            return

    raise HTTPException(status_code=403, detail="Documentation Pack management permission required")


async def _require_workspace_permission(request: Request, codename: str, detail: str) -> None:
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = int(payload.get("sub", 0))
    async with async_session_factory() as db:
        superadmin = await db.execute(
            text("SELECT is_superadmin FROM users WHERE id = :uid"), {"uid": user_id}
        )
        row = superadmin.fetchone()
        if row and row[0]:
            return
        result = await db.execute(text("""
            SELECT 1 FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = :uid AND p.codename = :codename
            LIMIT 1
        """), {"uid": user_id, "codename": codename})
        if result.fetchone():
            return
    raise HTTPException(status_code=403, detail=detail)


async def require_connector_view(request: Request) -> None:
    """Require safe connector catalogue/health visibility."""
    await _require_workspace_permission(request, "view_connectors", "Connector view permission required")


async def require_connector_management(request: Request) -> None:
    """Require workspace connector lifecycle authority."""
    await _require_workspace_permission(request, "manage_connectors", "Connector management permission required")
