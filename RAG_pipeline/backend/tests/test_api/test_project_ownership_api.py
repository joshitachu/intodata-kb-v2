"""Slice 2: ownership-scoped project endpoints, exercised against a real DB.

The existing admin tests use a mocked DB, which cannot exercise the ownership
queries. These use the in-memory SQLite ``db_session`` so ``can_manage_project``
runs for real.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.api.dependencies import get_current_user, get_db, get_qdrant
from src.db.models import Permission, ProjectAssignment, Role, User
from src.rbac.permissions import get_owned_project_ids

CSRF = {"cookie": "csrf_token=t", "X-CSRF-Token": "t"}


async def _seed_user(
    db: AsyncSession,
    username: str,
    *,
    superadmin: bool = False,
    perms: tuple[str, ...] = (),
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        is_superadmin=superadmin,
        mfa_enabled=True,
    )
    if perms:
        role = Role(name=f"role_{username}", description="")
        for codename in perms:
            role.permissions.append(Permission(codename=codename, description=""))
        user.roles.append(role)
    db.add(user)
    await db.flush()
    return user


def _override(db: AsyncSession, user: User) -> None:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_qdrant] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: user


def _patched_audit():
    """Patch AuditService so tests don't depend on audit-log side effects."""
    mock = MagicMock()
    mock.return_value.log = AsyncMock()
    return patch("src.api.routes.admin.AuditService", mock)


async def test_admin_creates_project_and_owns_it(db_session: AsyncSession) -> None:
    admin = await _seed_user(db_session, "admin_a", perms=("create_projects",))
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "proj-1"},
                    headers=CSRF,
                )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_owner"] is True
        assert await get_owned_project_ids(admin, db_session) == ["proj-1"]
    finally:
        app.dependency_overrides.clear()


async def test_duplicate_project_claim_rejected(db_session: AsyncSession) -> None:
    admin = await _seed_user(db_session, "admin_b", perms=("create_projects",))
    db_session.add(
        ProjectAssignment(user_id=admin.id, project_id="taken", is_owner=True)
    )
    await db_session.flush()
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "taken"},
                    headers=CSRF,
                )
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.clear()


async def test_admin_cannot_assign_project_they_dont_own(db_session: AsyncSession) -> None:
    admin = await _seed_user(db_session, "admin_c", perms=("assign_projects",))
    target = await _seed_user(db_session, "dev_c")
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/project-assignments",
                    json={"user_id": target.id, "project_id": "not-mine"},
                    headers=CSRF,
                )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_admin_can_assign_owned_project(db_session: AsyncSession) -> None:
    admin = await _seed_user(db_session, "admin_d", perms=("assign_projects",))
    target = await _seed_user(db_session, "dev_d")
    db_session.add(
        ProjectAssignment(user_id=admin.id, project_id="mine", is_owner=True)
    )
    await db_session.flush()
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/project-assignments",
                    json={"user_id": target.id, "project_id": "mine"},
                    headers=CSRF,
                )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_owner"] is False
    finally:
        app.dependency_overrides.clear()


async def test_superadmin_can_assign_any_project(db_session: AsyncSession) -> None:
    root = await _seed_user(db_session, "root_d", superadmin=True)
    target = await _seed_user(db_session, "dev_e")
    _override(db_session, root)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/project-assignments",
                    json={"user_id": target.id, "project_id": "anything"},
                    headers=CSRF,
                )
        assert resp.status_code == 201, resp.text
    finally:
        app.dependency_overrides.clear()
