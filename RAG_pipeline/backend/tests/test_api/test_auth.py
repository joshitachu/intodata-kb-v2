"""Tests for auth API endpoints (src/api/routes/auth.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.main import app
from src.auth.security import hash_password, hash_token
from src.db.models import (
    AccessRequest,
    EmailOTPCode,
    Permission,
    Role,
    User,
    UserInvite,
    UserOnboarding,
)


def _mock_user(is_superadmin: bool = True, mfa_enabled: bool = False):
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
    user.mfa_enabled = mfa_enabled
    user.totp_secret = None
    user.backup_codes_hashed = None
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

                with patch("src.api.routes.auth._onboarding_flags") as mock_flags:
                    mock_flags.return_value = (True, True)

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

    async def test_login_mfa_enabled_returns_challenge_without_cookies(self, mock_auth_deps):
        user = _mock_user(mfa_enabled=True)

        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.authenticate_user = AsyncMock(return_value=user)
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app, client=("127.0.0.2", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": "admin"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["mfa_required"] is True
        assert data["mfa_token"]
        assert "access_token" not in resp.cookies
        mock_svc.create_tokens.assert_not_called()

    async def test_login_wrong_password(self, mock_auth_deps):
        with patch("src.api.routes.auth.AuthService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.authenticate_user = AsyncMock(return_value=None)
            mock_svc_cls.return_value = mock_svc

            transport = ASGITransport(app=app, client=("127.0.0.3", 123))
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

            transport = ASGITransport(app=app, client=("127.0.0.2", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "nobody", "password": "pass"},
                )

        assert resp.status_code == 401

    async def test_login_accepts_email_identifier(self, db_session):
        from src.api.dependencies import get_db

        user = User(
            username="email-user",
            email="email-login@example.com",
            password_hash=hash_password("correct-password"),
        )
        db_session.add(user)
        await db_session.commit()

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app, client=("127.0.0.3", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "identifier": "EMAIL-LOGIN@example.com",
                        "password": "correct-password",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "email-user"
        assert "access_token" in resp.cookies

    async def test_passwordless_user_gets_clear_login_error(self, db_session):
        from src.api.dependencies import get_db

        user = User(
            username="passwordless",
            email="passwordless@example.com",
            password_hash=None,
        )
        db_session.add(user)
        await db_session.commit()

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app, client=("127.0.0.4", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "identifier": "passwordless@example.com",
                        "password": "anything",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 401
        assert resp.json()["detail"] == (
            "This account does not have a password. Use email code or contact an admin."
        )


class TestEmailOTPLogin:
    async def test_send_login_otp_stores_hash_only(self, db_session):
        from src.api.dependencies import get_db

        user = User(username="otp-user", email="otp@example.com", password_hash=None)
        db_session.add(user)
        await db_session.commit()

        captured: dict[str, str] = {}
        app.dependency_overrides[get_db] = lambda: db_session
        try:
            with patch("src.api.routes.auth.send_login_otp_email") as send_email:
                send_email.side_effect = lambda email, code: captured.update(
                    {"email": email, "code": code}
                )
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/auth/otp/send",
                        json={"email": "OTP@Example.com", "purpose": "login"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert captured["email"] == "otp@example.com"
        assert captured["code"].isdigit()
        assert len(captured["code"]) == 6
        otp = (await db_session.execute(select(EmailOTPCode))).scalar_one()
        assert otp.email == "otp@example.com"
        assert otp.purpose == "login"
        assert otp.code_hash != captured["code"]

    async def test_verify_login_otp_sets_session_cookies(self, db_session):
        from src.api.dependencies import get_db

        user = User(username="otp-user", email="otp@example.com", password_hash=None)
        db_session.add(user)
        await db_session.commit()

        captured: dict[str, str] = {}
        app.dependency_overrides[get_db] = lambda: db_session
        try:
            with patch("src.api.routes.auth.send_login_otp_email") as send_email:
                send_email.side_effect = lambda email, code: captured.update({"code": code})
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    await client.post(
                        "/api/v1/auth/otp/send",
                        json={"email": "otp@example.com", "purpose": "login"},
                    )
                    resp = await client.post(
                        "/api/v1/auth/otp/verify",
                        json={
                            "email": "otp@example.com",
                            "code": captured["code"],
                            "purpose": "login",
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "otp@example.com"
        assert "access_token" in resp.cookies
        otp = (await db_session.execute(select(EmailOTPCode))).scalar_one()
        assert otp.consumed_at is not None

    async def test_unknown_email_login_send_is_generic(self, db_session):
        from src.api.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            with patch("src.api.routes.auth.send_login_otp_email") as send_email:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/auth/otp/send",
                        json={"email": "missing@example.com", "purpose": "login"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"
        send_email.assert_not_called()


class TestMe:
    async def test_me_authenticated(self, mock_authed_deps):
        with patch("src.api.routes.auth._onboarding_flags") as mock_flags:
            mock_flags.return_value = (True, True)

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


class TestMandatoryMFA:
    async def test_me_is_exempt_before_mfa_enrollment(self, mock_auth_deps):
        mock_db = mock_auth_deps["db"]
        user = _mock_user(mfa_enabled=False)

        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        onboarding_result = MagicMock()
        onboarding_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[user_result, onboarding_result])

        with patch("src.api.dependencies.decode_token") as mock_decode:
            mock_decode.return_value = {"sub": "1", "type": "access"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/auth/me",
                    headers={"cookie": "access_token=access_tok"},
                )

        assert resp.status_code == 200
        assert resp.json()["mfa_enabled"] is False

    async def test_protected_routes_require_mfa_before_enrollment(self, mock_auth_deps):
        mock_db = mock_auth_deps["db"]
        user = _mock_user(mfa_enabled=False)

        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(return_value=user_result)

        with patch("src.api.dependencies.decode_token") as mock_decode:
            mock_decode.return_value = {"sub": "1", "type": "access"}
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/v1/admin/users",
                    headers={"cookie": "access_token=access_tok"},
                )

        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "mfa_required"


class TestAccessRequestsAndInvites:
    async def test_request_access_creates_pending_and_duplicate_is_idempotent(self, db_session):
        from src.api.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/request-access",
                    json={"email": "Person@Example.com", "full_name": "Person Example"},
                )
                dup = await client.post(
                    "/api/v1/auth/request-access",
                    json={"email": "person@example.com"},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert dup.status_code == 200
        assert dup.json()["status"] == "already_pending"
        rows = (await db_session.execute(select(AccessRequest))).scalars().all()
        assert len(rows) == 1
        assert rows[0].email == "person@example.com"

    async def test_accept_invite_creates_user_onboarding_and_invalidates_token(self, db_session):
        from datetime import datetime, timedelta, timezone
        from src.api.dependencies import get_db

        token = "invite-token-with-length"
        db_session.add(Role(name="developer", description="Developer"))
        db_session.add(
            UserInvite(
                email="new@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/invite/accept",
                    json={
                        "token": token,
                        "username": "newuser",
                        "password": "LongEnoughPassword123",
                        "full_name": "New User",
                        "role": "Engineer",
                        "intended_use": "Search project documentation",
                    },
                )
                reused = await client.post(
                    "/api/v1/auth/invite/accept",
                    json={
                        "token": token,
                        "username": "otheruser",
                        "password": "LongEnoughPassword123",
                        "full_name": "Other User",
                        "role": "Engineer",
                        "intended_use": "Search project documentation",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "new@example.com"
        assert resp.json()["user"]["mfa_enabled"] is False
        assert "access_token" in resp.cookies
        assert reused.status_code == 404

        user = (
            await db_session.execute(select(User).where(User.email == "new@example.com"))
        ).scalar_one()
        onboarding = (
            await db_session.execute(
                select(UserOnboarding).where(UserOnboarding.user_id == user.id)
            )
        ).scalar_one()
        invite = (await db_session.execute(select(UserInvite))).scalar_one()
        assert onboarding.wizard_completed is False
        assert invite.status == "accepted"
        assert invite.role_title == "Engineer"

    async def test_superadmin_can_approve_access_request_and_non_admin_cannot(self, db_session):
        from src.api.dependencies import get_current_user, get_db

        admin = User(
            username="admin",
            email="admin@example.com",
            password_hash="x",
            is_superadmin=True,
            mfa_enabled=True,
        )
        non_admin = User(
            username="user",
            email="user@example.com",
            password_hash="x",
            is_superadmin=False,
            mfa_enabled=True,
        )
        req = AccessRequest(email="invitee@example.com", status="pending")
        db_session.add_all([admin, non_admin, req])
        await db_session.commit()

        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: non_admin
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            forbidden = await client.post(f"/api/v1/admin/access-requests/{req.id}/approve")

        app.dependency_overrides[get_current_user] = lambda: admin
        try:
            with patch("src.api.routes.admin.send_invite_email") as send_invite:
                send_invite.return_value = "http://localhost:3000/onboarding?token=dev"
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    approved = await client.post(
                        f"/api/v1/admin/access-requests/{req.id}/approve"
                    )
        finally:
            app.dependency_overrides.clear()

        assert forbidden.status_code == 403
        assert approved.status_code == 200
        data = approved.json()
        assert data["request"]["status"] == "approved"
        assert data["request"]["invite"]["email"] == "invitee@example.com"
        invite = (await db_session.execute(select(UserInvite))).scalar_one()
        assert invite.status == "pending"
        assert invite.token_hash


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
