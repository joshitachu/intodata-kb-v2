"""Tests for auth API endpoints (src/api/routes/auth.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Permission, Role, User


def _mock_user(is_superadmin: bool = True):
    """Create a mock user for testing."""
    perm = MagicMock(spec=Permission)
    perm.codename = "manage_users"
    role = MagicMock(spec=Role)
    role.id = 1
    role.name = "admin"
    role.permissions = [perm]

    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.email = "admin@test.com"
    user.is_active = True
    user.is_superadmin = is_superadmin
    user.roles = [role]
    user.last_login_at = None
    return user


@pytest.fixture
def mock_auth_deps():
    """Override dependencies for auth testing (no user override — tests auth flow)."""
    from src.api.dependencies import get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    # NOT overriding get_current_user — tests hit auth middleware naturally

    yield {"db": mock_db, "qdrant": mock_qdrant}
    app.dependency_overrides.clear()


@pytest.fixture
def mock_authed_deps():
    """Override dependencies WITH an authenticated user."""
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


class TestLogin:
    async def test_login_success(self, mock_auth_deps):
        mock_db = mock_auth_deps["db"]
        user = _mock_user()

        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.authenticate_user = AsyncMock(return_value=user)
            mock_svc.create_tokens = AsyncMock(return_value=("access_tok", "refresh_tok"))
            mock_svc_cls.return_value = mock_svc

            with patch("src.api.routes.auth.AuditService") as mock_audit_cls:
                mock_audit = MagicMock()
                mock_audit.log = AsyncMock()
                mock_audit_cls.return_value = mock_audit

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/auth/login",
                        json={"username": "admin", "password": "admin"},
                    )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Login successful"
        assert data["user"]["username"] == "admin"
        # Check cookies are set
        cookies = resp.cookies
        assert "access_token" in cookies

    async def test_login_wrong_password(self, mock_auth_deps):
        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.authenticate_user = AsyncMock(return_value=None)
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": "wrongpass"},
                )

        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, mock_auth_deps):
        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.authenticate_user = AsyncMock(return_value=None)
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "nobody", "password": "pass"},
                )

        assert resp.status_code == 401


class TestMe:
    async def test_me_authenticated(self, mock_authed_deps):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/me")

        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["is_superadmin"] is True
        assert "permissions" in data

    async def test_me_unauthenticated(self, mock_auth_deps):
        """Without auth override, /me returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/me")

        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookies(self, mock_auth_deps):
        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.revoke_refresh_token = AsyncMock()
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/v1/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Logged out"


class TestRefresh:
    async def test_refresh_no_token(self, mock_auth_deps):
        """Refresh without cookie returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/auth/refresh")

        assert resp.status_code == 401

    async def test_refresh_token_success(self, mock_auth_deps):
        user = _mock_user()

        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.rotate_refresh_token = AsyncMock(
                return_value=("new_access", "new_refresh", user)
            )
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/refresh",
                    headers={"cookie": "refresh_token=old_refresh_tok"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Token refreshed"
