"""Invite acceptance honours the authority chosen at approval time.

``accept_invite`` used to hardcode the ``developer`` role for every new member,
which silently discarded a deliberate admin grant and meant role was only
decided after first-run onboarding had already rendered. These tests run against
a real session so the FK, the JSON column and the project assignments are all
exercised rather than mocked.
"""

from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.main import app
from src.auth.security import hash_token
from src.db.models import ProjectAssignment, Role, User, UserInvite


ACCEPT_BODY = {
    "username": "newuser",
    "password": "LongEnoughPassword123",
    "full_name": "New User",
    "role": "Engineer",
    "intended_use": "Search project documentation",
}


async def _accept(db_session, token: str, **overrides):
    from src.api.dependencies import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/auth/invite/accept",
                json={"token": token, **ACCEPT_BODY, **overrides},
            )
    finally:
        app.dependency_overrides.clear()


class TestInviteRoleApplied:
    async def test_admin_grant_on_the_invite_is_applied(self, db_session):
        """An invite approved as admin must not fall back to developer."""
        token = "admin-invite-token-value"
        admin_role = Role(name="admin", description="Administrator")
        db_session.add_all([admin_role, Role(name="developer", description="Developer")])
        await db_session.flush()

        db_session.add(
            UserInvite(
                email="promoted@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                assigned_role_id=admin_role.id,
            )
        )
        await db_session.commit()

        resp = await _accept(db_session, token)

        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["roles"] == ["admin"]

        user = (
            await db_session.execute(
                select(User).where(User.email == "promoted@example.com")
            )
        ).scalar_one()
        await db_session.refresh(user, ["roles"])
        assert [r.name for r in user.roles] == ["admin"]

    async def test_invite_without_a_role_uses_the_default_member_role(self, db_session):
        """Invites created before role-at-approval existed must still work."""
        token = "legacy-invite-token-value"
        db_session.add(Role(name="developer", description="Developer"))
        db_session.add(
            UserInvite(
                email="legacy@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        resp = await _accept(db_session, token)

        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["roles"] == ["developer"]

    async def test_a_deleted_role_falls_back_instead_of_failing_signup(self, db_session):
        """A role removed between approval and acceptance must not block signup.

        The FK is ON DELETE SET NULL, so deleting the granted role clears the
        invite's pointer rather than leaving it dangling. Acceptance then has to
        degrade to the default member role instead of creating a role-less user.
        """
        token = "dangling-invite-token-val"
        temporary_role = Role(name="contractor", description="Temporary")
        db_session.add_all(
            [temporary_role, Role(name="developer", description="Developer")]
        )
        await db_session.flush()

        db_session.add(
            UserInvite(
                email="dangling@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                assigned_role_id=temporary_role.id,
            )
        )
        await db_session.commit()

        await db_session.delete(temporary_role)
        await db_session.commit()

        resp = await _accept(db_session, token)

        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["roles"] == ["developer"]


class TestInviteProjectScope:
    async def test_pre_assigned_projects_are_granted_on_acceptance(self, db_session):
        token = "scoped-invite-token-value"
        db_session.add(Role(name="developer", description="Developer"))
        db_session.add(
            UserInvite(
                email="scoped@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                assigned_project_ids=["werkorder", "billing"],
            )
        )
        await db_session.commit()

        resp = await _accept(db_session, token)
        assert resp.status_code == 200, resp.text

        user = (
            await db_session.execute(
                select(User).where(User.email == "scoped@example.com")
            )
        ).scalar_one()
        rows = (
            await db_session.execute(
                select(ProjectAssignment).where(ProjectAssignment.user_id == user.id)
            )
        ).scalars().all()

        assert sorted(r.project_id for r in rows) == ["billing", "werkorder"]
        # Pre-scoping grants membership, never ownership: an approver handing
        # out access must not silently make someone a project owner.
        assert all(r.is_owner is False for r in rows)

    async def test_no_pre_assignment_grants_nothing(self, db_session):
        token = "unscoped-invite-token-val"
        db_session.add(Role(name="developer", description="Developer"))
        db_session.add(
            UserInvite(
                email="unscoped@example.com",
                token_hash=hash_token(token),
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        resp = await _accept(db_session, token)
        assert resp.status_code == 200, resp.text

        user = (
            await db_session.execute(
                select(User).where(User.email == "unscoped@example.com")
            )
        ).scalar_one()
        rows = (
            await db_session.execute(
                select(ProjectAssignment).where(ProjectAssignment.user_id == user.id)
            )
        ).scalars().all()
        assert rows == []
