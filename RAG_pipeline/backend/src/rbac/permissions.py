from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User

logger = logging.getLogger(__name__)


# --- Permission codename constants ---
MANAGE_USERS = "manage_users"
MANAGE_ROLES = "manage_roles"
ASSIGN_PROJECTS = "assign_projects"
VIEW_ALL_PROJECTS = "view_all_projects"
MANAGE_PROJECTS = "manage_projects"
VIEW_ASSIGNED_PROJECTS = "view_assigned_projects"
QUERY_RAG = "query_rag"
INGEST_DOCUMENTS = "ingest_documents"
VIEW_AUDIT_LOGS = "view_audit_logs"
CREATE_PROJECTS = "create_projects"
RUNTIME_SOURCES_READ = "runtime_sources:read"
RUNTIME_SOURCES_WRITE = "runtime_sources:write"
MANAGE_PACKS = "manage_packs"

# Role granted to a new member when no explicit authority was recorded for
# them. Invite acceptance and SSO first-login both need it, so it lives here
# rather than being spelled out as a literal at each call site.
DEFAULT_MEMBER_ROLE = "developer"

ALL_PERMISSIONS = {
    MANAGE_USERS,
    MANAGE_ROLES,
    ASSIGN_PROJECTS,
    VIEW_ALL_PROJECTS,
    MANAGE_PROJECTS,
    VIEW_ASSIGNED_PROJECTS,
    QUERY_RAG,
    INGEST_DOCUMENTS,
    VIEW_AUDIT_LOGS,
    CREATE_PROJECTS,
    RUNTIME_SOURCES_READ,
    RUNTIME_SOURCES_WRITE,
    MANAGE_PACKS,
}


def get_user_groups(user: User) -> list[str]:
    """Return the list of group names a user belongs to (from their roles).

    These map directly to the ``allowed_groups`` payload field stored in Qdrant
    vectors, enabling row-level access control on search results.
    """
    return [role.name for role in user.roles]


def get_user_permissions(user: User) -> set[str]:
    """Collect all permission codenames across the user's roles.

    Superadmins get all permissions.
    """
    if user.is_superadmin:
        return ALL_PERMISSIONS.copy()

    perms: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.codename)
    return perms


def has_permission(user: User, codename: str) -> bool:
    """Check if a user has a specific permission."""
    if user.is_superadmin:
        return True
    return codename in get_user_permissions(user)


def can_view_all_projects(user: User) -> bool:
    """Check if user can see all projects (superadmin or has view_all_projects)."""
    if user.is_superadmin:
        return True
    return has_permission(user, VIEW_ALL_PROJECTS)


async def get_user_project_ids(user: User, db: AsyncSession) -> list[str] | None:
    """Return the project_ids the user is allowed to access.

    Returns None if the user can see all projects (superadmin / view_all_projects).
    Returns a list of assigned project_ids otherwise.
    """
    if can_view_all_projects(user):
        return None  # No restriction

    from src.db.models import ProjectAssignment

    result = await db.execute(
        select(ProjectAssignment.project_id).where(
            ProjectAssignment.user_id == user.id
        )
    )
    project_ids = list(result.scalars().all())
    logger.debug("User %s has access to projects: %s", user.username, project_ids)
    return project_ids


async def get_owned_project_ids(user: User, db: AsyncSession) -> list[str]:
    """Return the project_ids the user owns (created).

    Ownership is the ``is_owner`` flag on ``ProjectAssignment``. Superadmins are
    not implicitly owners of every project — ownership is about *management*
    rights (assign/scan/generate), which superadmins already get via their
    global bypass, so callers should check ``is_superadmin`` first where a
    global override is intended.
    """
    from src.db.models import ProjectAssignment

    result = await db.execute(
        select(ProjectAssignment.project_id).where(
            ProjectAssignment.user_id == user.id,
            ProjectAssignment.is_owner.is_(True),
        )
    )
    return list(result.scalars().all())


async def can_manage_project(user: User, db: AsyncSession, project_id: str) -> bool:
    """Whether the user may manage (assign/scan/generate/push) a given project.

    Superadmins can manage any project; everyone else must own it. This is the
    single ownership gate the project endpoints should call — do not re-derive
    ownership inline.
    """
    if user.is_superadmin:
        return True
    owned = await get_owned_project_ids(user, db)
    return project_id in owned
