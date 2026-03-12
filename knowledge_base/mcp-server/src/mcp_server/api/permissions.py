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

    Access is granted if: superadmin, has view_all_projects or manage_projects
    permission, or has a project_assignment row for this project.
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

        # Check view_all_projects or manage_projects permission
        perm_result = await db.execute(text("""
            SELECT 1 FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = :uid AND p.codename IN ('view_all_projects', 'manage_projects')
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
