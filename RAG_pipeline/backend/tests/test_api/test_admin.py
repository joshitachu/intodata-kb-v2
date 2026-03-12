"""Tests for admin API endpoints (src/api/routes/admin.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import (
    AuditLog,
    Permission,
    ProjectAssignment,
    QueryUsage,
    Role,
    User,
)


def _mock_admin_user():
    """Create a mock superadmin user."""
    role = MagicMock(spec=Role)
    role.id = 1
    role.name = "admin"
    perm = MagicMock(spec=Permission)
    perm.codename = "manage_users"
    role.permissions = [perm]

    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.email = "admin@test.com"
    user.is_active = True
    user.is_superadmin = True
    user.roles = [role]
    user.last_login_at = None
    return user


def _mock_regular_user():
    """Create a mock non-admin user."""
    role = MagicMock(spec=Role)
    role.id = 2
    role.name = "developer"
    role.permissions = []

    user = MagicMock(spec=User)
    user.id = 2
    user.username = "dev"
    user.email = "dev@test.com"
    user.is_active = True
    user.is_superadmin = False
    user.roles = [role]
    user.last_login_at = None
    return user


@pytest.fixture
def mock_admin_deps():
    """Override dependencies with an admin user."""
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_admin_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


@pytest.fixture
def mock_noauth_deps():
    """Override dependencies WITHOUT auth (to test 401/403)."""
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_regular_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


class TestListUsers:
    async def test_list_users_as_admin(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]
        admin = _mock_admin_user()

        # Mock paginated query + efficient count query
        users_result = MagicMock()
        users_result.scalars.return_value.all.return_value = [admin]
        total_result = MagicMock()
        total_result.scalar_one.return_value = 1

        mock_db.execute = AsyncMock(side_effect=[users_result, total_result])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/users")

        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert data["total"] == 1

    async def test_list_users_unauthorized(self, mock_noauth_deps):
        """Non-superadmin gets 403."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/users")

        assert resp.status_code == 403


class TestCreateUser:
    async def test_create_user(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        # No existing user
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=check_result)

        captured = []

        def capture_add(obj):
            captured.append(obj)
            if hasattr(obj, "id") and obj.id is None:
                obj.id = 99
                obj.is_active = True
                obj.is_superadmin = False
                obj.roles = []
                obj.last_login_at = None

        mock_db.add = capture_add
        mock_db.flush = AsyncMock()

        with patch("src.api.routes.admin.hash_password", return_value="hashed"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/users",
                    json={
                        "username": "newuser",
                        "email": "new@test.com",
                        "password": "securepass",
                    },
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"

    async def test_create_user_duplicate(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        existing = MagicMock(spec=User)
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=check_result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/users",
                json={
                    "username": "existing",
                    "email": "existing@test.com",
                    "password": "password1234",
                },
            )

        assert resp.status_code == 409


class TestRoles:
    async def test_list_roles(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        role = MagicMock(spec=Role)
        role.id = 1
        role.name = "admin"
        role.description = "Administrator"
        role.permissions = []

        result = MagicMock()
        result.scalars.return_value.all.return_value = [role]
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/roles")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["roles"]) == 1
        assert data["roles"][0]["name"] == "admin"

    async def test_create_role(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        # No existing role
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=check_result)

        def capture_add(obj):
            obj.id = 5
            obj.permissions = []

        mock_db.add = capture_add
        mock_db.flush = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/roles",
                json={"name": "viewer", "description": "Read-only role"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "viewer"

    async def test_delete_admin_role_blocked(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        role = MagicMock(spec=Role)
        role.id = 1
        role.name = "admin"

        result = MagicMock()
        result.scalar_one_or_none.return_value = role
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/admin/roles/1")

        assert resp.status_code == 400
        assert "admin" in resp.json()["detail"].lower()


class TestProjectAssignments:
    async def test_create_project_assignment(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        # Mock user lookup
        user = MagicMock(spec=User)
        user.id = 2
        user.username = "dev"
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user

        # No existing assignment
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None

        mock_db.execute = AsyncMock(side_effect=[user_result, check_result])

        import datetime

        def capture_add(obj):
            if hasattr(obj, "project_id"):
                obj.id = 10
                obj.assigned_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        mock_db.add = capture_add
        mock_db.flush = AsyncMock()

        with patch("src.api.routes.admin.AuditService") as mock_audit_cls:
            mock_audit = MagicMock()
            mock_audit.log = AsyncMock()
            mock_audit_cls.return_value = mock_audit

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/project-assignments",
                    json={"user_id": 2, "project_id": "project-alpha"},
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["project_id"] == "project-alpha"
        assert data["user_id"] == 2


class TestAuditLogs:
    async def test_list_audit_logs(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        import datetime

        log = MagicMock(spec=AuditLog)
        log.id = 1
        log.user_id = 1
        log.username = "admin"
        log.action = "login"
        log.resource_type = "session"
        log.resource_id = None
        log.details = None
        log.ip_address = "127.0.0.1"
        log.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        # Efficient count query + paginated query
        total_result = MagicMock()
        total_result.scalar_one.return_value = 1
        page_result = MagicMock()
        page_result.scalars.return_value.all.return_value = [log]

        mock_db.execute = AsyncMock(side_effect=[total_result, page_result])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/audit-logs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["logs"]) == 1
        assert data["logs"][0]["action"] == "login"


class TestUsage:
    async def test_usage_summary(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        # 3 execute calls: by_interface, by_model, daily
        empty_result = MagicMock()
        empty_result.__iter__ = lambda self: iter([])

        mock_db.execute = AsyncMock(return_value=empty_result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/usage/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert "by_interface" in data
        assert "by_model" in data
        assert "daily" in data
        assert data["days"] == 30

    async def test_usage_recent(self, mock_admin_deps):
        mock_db = mock_admin_deps["db"]

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/usage/recent")

        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data


class TestAdminEndpointsRequireAuth:
    async def test_admin_endpoints_require_admin(self, mock_noauth_deps):
        """All admin endpoints return 403 for non-admin users."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            endpoints = [
                ("GET", "/api/v1/admin/users"),
                ("GET", "/api/v1/admin/roles"),
                ("GET", "/api/v1/admin/permissions"),
            ]
            for method, url in endpoints:
                if method == "GET":
                    resp = await client.get(url)
                assert resp.status_code == 403, f"{method} {url} should return 403, got {resp.status_code}"
