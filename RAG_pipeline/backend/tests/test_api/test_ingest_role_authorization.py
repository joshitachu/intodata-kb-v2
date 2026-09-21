"""Uploading a non-source document is a management act, not a contribution.

Project access says a user may add evidence to the project. It does not say they
may change how the project is answered -- and a reference, guidance, standard,
template or policy document does exactly that, because those roles become the
methods and rules the assistant applies for everyone on the project. Without a
second gate, "document_role=policy" on an upload form is an escalation path open
to any member.

These tests pin the gate in both directions: that the five non-source roles are
refused without ``manage_projects``, and that source uploads stay open to every
member, because a gate that also blocks the ordinary path is a different bug.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Permission, Role, User
from src.rbac.permissions import MANAGE_PROJECTS, VIEW_ALL_PROJECTS

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}

PROJECT_ID = "proj"
NON_SOURCE_ROLES = ("reference", "guidance", "standard", "template", "policy")


def _permission(codename: str) -> Permission:
    perm = MagicMock(spec=Permission)
    perm.codename = codename
    return perm


def _user(*, permissions: tuple[str, ...] = (), is_superadmin: bool = False) -> User:
    role = MagicMock(spec=Role)
    role.name = "engineering"
    role.permissions = [_permission(codename) for codename in permissions]
    user = MagicMock(spec=User)
    user.id = 1
    user.is_superadmin = is_superadmin
    user.roles = [role]
    return user


def _db_with_project_assignment(project_id: str = PROJECT_ID) -> AsyncMock:
    """A session that reports the user is assigned to exactly one project."""

    result = MagicMock()
    result.scalars.return_value.all.return_value = [project_id]
    db = AsyncMock()
    db.execute.return_value = result
    return db


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The upload route is rate limited per client address, and every test here
    shares one. Without a reset the later cases would fail on the limiter
    instead of on the authorization rule they exist to check."""

    from src.api.routes.auth import limiter

    limiter.reset()
    yield
    limiter.reset()


async def _upload(user: User, *, document_role: str, db: AsyncMock | None = None):
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    doc = MagicMock()
    doc.id = 7
    doc.filename = f"{PROJECT_ID}/policy.md"
    job = MagicMock()
    job.status = "completed"
    job.chunks_created = 1

    app.dependency_overrides[get_db] = lambda: db or _db_with_project_assignment()
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch(
            "src.api.routes.ingest.ingest_document", new_callable=AsyncMock
        ) as ingest:
            ingest.return_value = (doc, job)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/ingest",
                    headers=CSRF_HEADERS,
                    files={"file": ("policy.md", b"# Policy\n\nClause 1.", "text/markdown")},
                    data={"project_id": PROJECT_ID, "document_role": document_role},
                )
            return resp, ingest
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("document_role", NON_SOURCE_ROLES)
async def test_project_member_cannot_upload_a_non_source_role(document_role: str) -> None:
    resp, ingest = await _upload(_user(), document_role=document_role)

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    # The refusal has to be actionable: it names the permission that is missing
    # and the role that would have worked. "Forbidden" alone sends the uploader
    # to support.
    assert MANAGE_PROJECTS in detail
    assert document_role in detail
    assert "source" in detail
    ingest.assert_not_awaited()


async def test_project_member_can_still_upload_source() -> None:
    resp, ingest = await _upload(_user(), document_role="source")

    assert resp.status_code == 200
    assert ingest.await_args.kwargs["document_role"] == "source"


@pytest.mark.parametrize("document_role", NON_SOURCE_ROLES)
async def test_manage_projects_permits_a_non_source_role(document_role: str) -> None:
    resp, ingest = await _upload(
        _user(permissions=(MANAGE_PROJECTS,)), document_role=document_role
    )

    assert resp.status_code == 200
    assert ingest.await_args.kwargs["document_role"] == document_role


async def test_superadmin_passes_without_an_explicit_grant() -> None:
    """Not special-cased in the route: ``has_permission`` already returns True
    for a superadmin, so the gate needs no second branch for them."""

    superadmin = _user(is_superadmin=True)
    assert superadmin.roles[0].permissions == []

    # A superadmin sees every project, so get_user_project_ids short-circuits
    # before it queries assignments; the session is never asked anything.
    resp, ingest = await _upload(superadmin, document_role="policy", db=AsyncMock())

    assert resp.status_code == 200
    assert ingest.await_args.kwargs["document_role"] == "policy"


async def test_the_project_access_check_is_unchanged() -> None:
    """The role gate is additional. A user outside the project is still refused
    on project access, and must not be told about the permission instead."""

    db = _db_with_project_assignment("other-project")
    resp, ingest = await _upload(_user(), document_role="source", db=db)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "No access to this project"
    ingest.assert_not_awaited()


async def test_a_project_wide_viewer_without_manage_projects_is_still_refused() -> None:
    """view_all_projects widens which projects are reachable, not what may be
    uploaded into them -- the two permissions answer different questions."""

    resp, _ingest = await _upload(
        _user(permissions=(VIEW_ALL_PROJECTS,)), document_role="guidance"
    )

    assert resp.status_code == 403
    assert MANAGE_PROJECTS in resp.json()["detail"]
