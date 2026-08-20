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

    # Return every project the caller can access. New projects must be selectable
    # so their first document can be ingested; filtering to projects that already
    # have documents creates an impossible first-upload loop.
    if project_ids is None:
        # Admin/superadmin: every registered project.
        rows = await db.execute(
            text("""
                SELECT p.id, p.display_name, p.platform, COUNT(d.id) AS doc_count
                FROM projects p
                LEFT JOIN documents d ON d.project_id = p.id
                GROUP BY p.id, p.display_name, p.platform
                ORDER BY p.display_name
            """)
        )
    else:
        # Regular user: only assigned projects, including empty ones.
        rows = await db.execute(
            text("""
                SELECT p.id, p.display_name, p.platform, COUNT(d.id) AS doc_count
                FROM projects p
                LEFT JOIN documents d ON d.project_id = p.id
                WHERE p.id = ANY(:pids)
                GROUP BY p.id, p.display_name, p.platform
                ORDER BY p.display_name
            """),
            {"pids": project_ids},
        )

    return {
        "projects": [
            {"id": r[0], "display_name": r[1], "platform": r[2], "doc_count": r[3]}
            for r in rows.fetchall()
        ]
    }
