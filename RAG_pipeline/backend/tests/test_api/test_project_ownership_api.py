"""Project creation and assignment endpoints, exercised against a real DB.

The existing admin tests use a mocked DB, which cannot exercise the ownership
queries. These use the in-memory SQLite ``db_session`` so project ownership and
assignment authorization run for real.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.api.dependencies import get_current_user, get_db, get_qdrant
from src.db.models import Document, Permission, ProjectAssignment, Role, User
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


async def test_admin_cannot_claim_project_with_documents(db_session: AsyncSession) -> None:
    """Security: an admin must not seize a pre-existing (scanner/dashboard) project
    that has documents but no owner row."""
    admin = await _seed_user(db_session, "admin_claim_docs", perms=("create_projects",))
    db_session.add(
        Document(filename="a.pdf", file_type="pdf", project_id="legacy-proj")
    )
    await db_session.flush()
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "legacy-proj"},
                    headers=CSRF,
                )
        assert resp.status_code == 409
        assert await get_owned_project_ids(admin, db_session) == []
    finally:
        app.dependency_overrides.clear()


async def test_admin_cannot_claim_project_assigned_to_others(db_session: AsyncSession) -> None:
    admin = await _seed_user(db_session, "admin_claim_asg", perms=("create_projects",))
    other = await _seed_user(db_session, "other_member")
    db_session.add(
        ProjectAssignment(user_id=other.id, project_id="shared-proj", is_owner=False)
    )
    await db_session.flush()
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "shared-proj"},
                    headers=CSRF,
                )
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.clear()


async def test_claiming_own_existing_assignment_is_clean_409(db_session: AsyncSession) -> None:
    """Correctness: creator already has a member assignment → clean 409, not a 500
    IntegrityError on the (user_id, project_id) unique constraint."""
    admin = await _seed_user(db_session, "admin_dup", perms=("create_projects",))
    db_session.add(
        ProjectAssignment(user_id=admin.id, project_id="dup-proj", is_owner=False)
    )
    await db_session.flush()
    _override(db_session, admin)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "dup-proj"},
                    headers=CSRF,
                )
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.clear()


async def test_superadmin_can_adopt_preexisting_project(db_session: AsyncSession) -> None:
    """Superadmin bypasses the pre-existing guard (can adopt scanner/dashboard projects)."""
    root = await _seed_user(db_session, "root_adopt", superadmin=True)
    db_session.add(
        Document(filename="b.pdf", file_type="pdf", project_id="adopt-proj")
    )
    await db_session.flush()
    _override(db_session, root)
    try:
        with _patched_audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/projects",
                    json={"project_id": "adopt-proj"},
                    headers=CSRF,
                )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_owner"] is True
    finally:
        app.dependency_overrides.clear()


async def test_admin_can_assign_project_they_dont_own(db_session: AsyncSession) -> None:
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
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_owner"] is False
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
