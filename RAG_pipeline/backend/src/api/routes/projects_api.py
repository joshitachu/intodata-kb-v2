"""Projects API — lists projects accessible to the current user (for project selector)."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from src.api.dependencies import CurrentUser, DBSession
from src.rbac.permissions import get_user_project_ids

logger = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])


@router.get("/projects")
async def list_user_projects(user: CurrentUser, db: DBSession):
    """List projects accessible to the current user (for project selector dropdown)."""
    project_ids = await get_user_project_ids(user, db)

    # Only return projects that have at least one ingested document
    if project_ids is None:
        # Admin/superadmin: all projects with documents
        rows = await db.execute(
            text("""
                SELECT p.id, p.display_name, p.platform
                FROM projects p
                WHERE EXISTS (SELECT 1 FROM documents d WHERE d.project_id = p.id)
                ORDER BY p.display_name
            """)
        )
    else:
        # Regular user: only assigned projects with documents
        rows = await db.execute(
            text("""
                SELECT p.id, p.display_name, p.platform
                FROM projects p
                WHERE p.id = ANY(:pids)
                  AND EXISTS (SELECT 1 FROM documents d WHERE d.project_id = p.id)
                ORDER BY p.display_name
            """),
            {"pids": project_ids},
        )

    return {
        "projects": [
            {"id": r[0], "display_name": r[1], "platform": r[2]}
            for r in rows.fetchall()
        ]
    }
