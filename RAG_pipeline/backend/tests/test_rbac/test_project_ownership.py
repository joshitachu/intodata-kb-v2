"""Slice 1: project ownership helpers for the superadmin/admin split."""
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ProjectAssignment, User
from src.rbac.permissions import (
    ALL_PERMISSIONS,
    CREATE_PROJECTS,
    can_manage_project,
    get_owned_project_ids,
)


def test_create_projects_in_all_permissions() -> None:
    assert CREATE_PROJECTS in ALL_PERMISSIONS


async def _make_user(db: AsyncSession, username: str, superadmin: bool = False) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        is_superadmin=superadmin,
    )
    db.add(user)
    await db.flush()
    return user


async def test_get_owned_project_ids_returns_only_owned(db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, "admin_owner")
    db_session.add_all(
        [
            ProjectAssignment(user_id=admin.id, project_id="proj-owned", is_owner=True),
            ProjectAssignment(user_id=admin.id, project_id="proj-member", is_owner=False),
        ]
    )
    await db_session.flush()

    owned = await get_owned_project_ids(admin, db_session)
    assert owned == ["proj-owned"]


async def test_can_manage_project_requires_ownership(db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, "admin_mgr")
    db_session.add(
        ProjectAssignment(user_id=admin.id, project_id="proj-x", is_owner=True)
    )
    await db_session.flush()

    assert await can_manage_project(admin, db_session, "proj-x") is True
    # A project the admin does not own (even if it exists) is not manageable.
    assert await can_manage_project(admin, db_session, "proj-other") is False


async def test_can_manage_project_superadmin_bypass(db_session: AsyncSession) -> None:
    superadmin = await _make_user(db_session, "root", superadmin=True)
    # No assignments at all — superadmin still manages any project.
    assert await can_manage_project(superadmin, db_session, "any-project") is True
