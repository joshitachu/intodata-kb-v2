"""Role assignment at access-approval time (src/api/routes/admin.py).

Approving access used to send an invite carrying no authority, so acceptance
fell back to a hardcoded ``developer`` role. These tests pin the behaviour that
replaced it: the approver's choice is recorded on the invite, Owner authority
still cannot be handed out through an invitation, and pre-scoping projects
requires the permission that normally governs it.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import AccessRequest, Permission, Role, User


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _owner_user():
    role = MagicMock(spec=Role)
    role.id = 1
    role.name = "admin"
    role.permissions = []

    user = MagicMock(spec=User)
    user.id = 1
    user.username = "owner"
    user.email = "owner@test.com"
    user.is_active = True
    user.is_superadmin = True
    user.roles = [role]
    return user


def _member_admin_user(*, permissions: list[str]):
    """A non-Owner admin holding an explicit permission set."""
    perms = []
    for codename in permissions:
        perm = MagicMock(spec=Permission)
        perm.codename = codename
        perms.append(perm)

    role = MagicMock(spec=Role)
    role.id = 3
    role.name = "admin"
    role.permissions = perms

    user = MagicMock(spec=User)
    user.id = 7
    user.username = "member-admin"
    user.email = "member-admin@test.com"
    user.is_active = True
    user.is_superadmin = False
    user.roles = [role]
    return user


def _pending_request():
    row = MagicMock(spec=AccessRequest)
    row.id = 11
    row.email = "newcomer@test.com"
    row.full_name = "New Comer"
    row.status = "pending"
    row.rejection_reason = None
    row.reviewed_by = None
    row.reviewed_at = None
    row.invite_id = None
    row.invite = None
    row.created_at = MagicMock()
    row.created_at.isoformat.return_value = "2026-08-27T10:00:00+00:00"
    return row


def _assignable_role(role_id: int, name: str):
    role = MagicMock(spec=Role)
    role.id = role_id
    role.name = name
    role.permissions = []
    return role


def _override(user):
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    return mock_db


def _result(value):
    """Wrap a value as a scalar_one_or_none() result."""
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


def _scalars(values):
    res = MagicMock()
    res.scalars.return_value.all.return_value = values
    return res


def _capture_added(mock_db) -> list:
    """Collect objects handed to ``db.add``, stamping DB-side defaults.

    ``UserInvite.created_at`` comes from a server default that a real flush
    fills in. The mocked session never issues SQL, so the test stands in for
    the database here rather than the route being made tolerant of an unsaved
    row it would never see in practice.
    """
    added: list = []

    def _add(obj):
        if getattr(obj, "token_hash", None) is not None and obj.created_at is None:
            obj.created_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        added.append(obj)

    mock_db.add = MagicMock(side_effect=_add)
    return added


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class TestApproveWithRole:
    async def test_chosen_role_is_recorded_on_the_invite(self):
        """The approver's role choice must reach the invite, not just the reply."""
        request_row = _pending_request()
        admin_role = _assignable_role(2, "admin")
        mock_db = _override(_owner_user())
        mock_db.execute = AsyncMock(
            side_effect=[
                _result(request_row),      # load access request
                _result(None),             # no existing user with this email
                _scalars([admin_role]),    # _load_assignable_roles
            ]
        )

        captured = _capture_added(mock_db)

        with patch(
            "src.api.routes.admin.send_invite_email",
            return_value="http://test/onboarding?token=x",
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    json={"role_id": 2, "welcome_note": "Joining the platform team"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        invites = [o for o in captured if getattr(o, "token_hash", None) is not None]
        assert len(invites) == 1
        invite = invites[0]
        assert invite.assigned_role_id == 2
        assert invite.assigned_role is admin_role
        assert invite.welcome_note == "Joining the platform team"
        assert resp.json()["message"] == "Invite sent as admin"

    async def test_approving_without_a_body_still_works(self):
        """An existing caller that sends no body must keep approving access."""
        request_row = _pending_request()
        mock_db = _override(_owner_user())
        mock_db.execute = AsyncMock(
            side_effect=[_result(request_row), _result(None)]
        )
        captured = _capture_added(mock_db)

        with patch("src.api.routes.admin.send_invite_email", return_value="http://x"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        invite = next(o for o in captured if getattr(o, "token_hash", None) is not None)
        # No role recorded — acceptance applies the default member role.
        assert invite.assigned_role_id is None
        assert resp.json()["message"] == "Invite sent"

    async def test_owner_authority_cannot_be_granted_by_invite(self):
        """A role named like Owner must be refused, as in member administration."""
        request_row = _pending_request()
        owner_role = _assignable_role(9, "owner")
        mock_db = _override(_owner_user())
        mock_db.execute = AsyncMock(
            side_effect=[
                _result(request_row),
                _result(None),
                _scalars([owner_role]),
            ]
        )

        with patch("src.api.routes.admin.send_invite_email", return_value="http://x"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    json={"role_id": 9},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 403
        assert "Owner authority" in resp.json()["detail"]

    async def test_unknown_role_is_rejected(self):
        request_row = _pending_request()
        mock_db = _override(_owner_user())
        mock_db.execute = AsyncMock(
            side_effect=[
                _result(request_row),
                _result(None),
                _scalars([]),  # requested id resolves to nothing
            ]
        )

        with patch("src.api.routes.admin.send_invite_email", return_value="http://x"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    json={"role_id": 4242},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 400

    async def test_pre_scoping_projects_needs_assign_projects(self):
        """Approving access must not become a way around assign_projects."""
        request_row = _pending_request()
        mock_db = _override(_member_admin_user(permissions=["manage_users"]))
        mock_db.execute = AsyncMock(
            side_effect=[_result(request_row), _result(None)]
        )

        with patch("src.api.routes.admin.send_invite_email", return_value="http://x"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    json={"project_ids": ["werkorder"]},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 403
        assert "assign_projects" in resp.json()["detail"]

    async def test_admin_with_assign_projects_may_pre_scope(self):
        request_row = _pending_request()
        mock_db = _override(
            _member_admin_user(permissions=["manage_users", "assign_projects"])
        )
        mock_db.execute = AsyncMock(
            side_effect=[_result(request_row), _result(None)]
        )
        captured = _capture_added(mock_db)

        with patch("src.api.routes.admin.send_invite_email", return_value="http://x"):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/admin/access-requests/11/approve",
                    # Blank entries are operator noise, not a project.
                    json={"project_ids": ["werkorder", "  ", ""]},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        invite = next(o for o in captured if getattr(o, "token_hash", None) is not None)
        assert invite.assigned_project_ids == ["werkorder"]
