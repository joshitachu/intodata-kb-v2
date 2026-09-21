"""Showing a user the agent's real worktree, and where their upload will land.

Both endpoints exist because the guidance stack was previously only describable,
never inspectable: the UI drew an abstract "layers" picture that was a second
account of the same thing and drifted from it. So the properties worth pinning
here are not the JSON shapes but the claims those shapes make --

* the worktree is the *real* one (same compile functions a turn uses), and
  reading it is a read: no directory is created, nothing is written;
* the MCP bearer, which lives in the workspace on disk, is not in the response;
* the routing table is derived, so it covers every role that exists today and
  will cover one added tomorrow.

Exercised against the in-memory SQLite ``db_session`` rather than a mocked
session, following ``test_guidance_routes.py``: project scoping and the material
compile are both query results, and a mock would only assert that the route
called the mock.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Document, Permission, ProjectAssignment, Role, User
from src.db.models.document_text import DocumentTextRow
from src.document_roles import DOCUMENT_ROLES
from src.harness.guidance.material import MATERIALIZE_MAX_CHARS, should_materialize
from src.labs.workspace import ORIGIN_ASSEMBLED, ensure_labs_workspace

PROJECT = "reverse_engineer_demo"
OTHER_PROJECT = "someone_elses_landscape"

MCP_TOKEN = "labs-workspace-route-bearer"
METHOD_TEXT = "1. Read the koppelvlak contract.\n2. Check the last PROD run.\n"
METHOD_DOCUMENT_ID = 52


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def labs_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Throwaway workspace and docs roots.

    Autouse because the defaults point at a developer's real checkout: a test
    that forgot this fixture would answer ``docs_mounted`` from whatever happens
    to be published on the machine running it, and would be free to create a
    workspace under ``/tmp`` that outlives the run.
    """

    monkeypatch.setenv("LABS_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LABS_DOCS_ROOT", str(tmp_path / "published"))
    return tmp_path


async def _seed_user(
    db: AsyncSession,
    username: str,
    *,
    superadmin: bool = False,
    perms: tuple[str, ...] = (),
    projects: tuple[str, ...] = (),
) -> User:
    """A user with exactly the authority the test needs and nothing more.

    ``roles`` and ``permissions`` are always assigned, even when empty: the
    permission checks walk both collections synchronously, and an untouched lazy
    relationship on an async session raises ``MissingGreenlet`` rather than
    returning "no permissions".
    """

    role = Role(name=f"role_{username}", description="")
    role.permissions = [Permission(codename=codename, description="") for codename in perms]
    user = User(
        username=username,
        email=f"{username}@example.com",
        is_superadmin=superadmin,
        mfa_enabled=True,
    )
    user.roles = [role]
    db.add(user)
    await db.flush()
    for project_id in projects:
        db.add(ProjectAssignment(user_id=user.id, project_id=project_id))
    await db.flush()
    return user


async def _seed_method_document(db: AsyncSession, project_id: str = PROJECT) -> None:
    """One uploaded Guidance document, small enough to be mounted.

    The id and filename are fixed so the assertion below can name the path the
    routing table promises -- which is the whole claim the feature makes to a
    user staring at an upload form.
    """

    db.add(
        Document(
            id=METHOD_DOCUMENT_ID,
            filename="test.docx",
            file_type="docx",
            document_role="guidance",
            project_id=project_id,
        )
    )
    await db.flush()
    db.add(
        DocumentTextRow(
            document_id=METHOD_DOCUMENT_ID,
            text=METHOD_TEXT,
            char_count=len(METHOD_TEXT),
            sha256=hashlib.sha256(METHOD_TEXT.encode("utf-8")).hexdigest(),
        )
    )
    await db.flush()


@pytest.fixture
def as_user(db_session: AsyncSession):
    """Authenticate the app as a seeded user, and clear the overrides afterwards."""

    def _use(user: User) -> None:
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user

    yield _use
    app.dependency_overrides.clear()


# -- the workspace endpoint --------------------------------------------------


async def test_a_caller_without_access_to_the_project_is_refused(
    db_session: AsyncSession, as_user
) -> None:
    # Same gate as `GET /projects/{id}`: answering "here is the workspace" for a
    # landscape the caller cannot see would disclose its rules, its vocabulary
    # and the filenames of its uploaded material.
    outsider = await _seed_user(db_session, "outsider", projects=(OTHER_PROJECT,))
    as_user(outsider)

    async with _client() as client:
        refused = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["code"] == "forbidden"


async def test_the_workspace_shows_the_files_a_turn_would_actually_get(
    db_session: AsyncSession, as_user
) -> None:
    member = await _seed_user(db_session, "member", projects=(PROJECT,))
    await _seed_method_document(db_session)
    as_user(member)

    async with _client() as client:
        response = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == PROJECT
    by_path = {file["path"]: file for file in body["files"]}

    agents = by_path["AGENTS.md"]
    assert agents["origin"] == ORIGIN_ASSEMBLED
    assert agents["always_loaded"] is True
    assert agents["bytes"] == len(agents["content"].encode("utf-8"))

    # The routing claim, made good: a Guidance upload became a skill file at the
    # path the document-routing endpoint advertises.
    skill = by_path[f".nexus/project/skills/{METHOD_DOCUMENT_ID}-test/SKILL.md"]
    assert skill["origin"] == f"document:{METHOD_DOCUMENT_ID}"
    assert skill["always_loaded"] is False
    assert "Read the koppelvlak contract." in skill["content"]

    # No published docs for this project in the throwaway root.
    assert body["docs_mounted"] is False


async def test_a_superadmin_sees_the_material_rather_than_an_empty_manifest(
    db_session: AsyncSession, as_user
) -> None:
    # `get_user_project_ids` answers None for an unrestricted caller, and
    # `compile_project_material` fails closed unless the project id is in the
    # caller's group set. Composing that list as `scope or ()` would leave every
    # superadmin looking at a workspace with no material in it.
    owner = await _seed_user(db_session, "owner", superadmin=True)
    await _seed_method_document(db_session)
    as_user(owner)

    async with _client() as client:
        response = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    assert response.status_code == 200, response.text
    paths = {file["path"] for file in response.json()["files"]}
    assert f".nexus/project/skills/{METHOD_DOCUMENT_ID}-test/SKILL.md" in paths
    assert ".nexus/project/MANIFEST.md" in paths


async def test_the_response_carries_no_credential(
    db_session: AsyncSession, as_user, labs_roots: Path
) -> None:
    # A real workspace exists on disk with a real bearer in `opencode.json`.
    # The endpoint projects the layout instead of reading that directory, so
    # neither the file nor the token may appear anywhere in the response.
    await ensure_labs_workspace(
        conversation_id=991, project_id=PROJECT, mcp_token=MCP_TOKEN
    )
    workspace = next((labs_roots / "workspaces").glob("conversation-*"))
    assert MCP_TOKEN in (workspace / "opencode.json").read_text(encoding="utf-8")

    member = await _seed_user(db_session, "member_secret", projects=(PROJECT,))
    as_user(member)
    async with _client() as client:
        response = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    assert response.status_code == 200, response.text
    raw = response.text
    assert "opencode.json" not in raw
    assert MCP_TOKEN not in raw
    assert "Bearer" not in raw
    assert "Authorization" not in raw


async def test_reading_the_workspace_writes_nothing(
    db_session: AsyncSession, as_user, labs_roots: Path
) -> None:
    # A GET that creates a per-conversation directory is a GET that fills a disk
    # when someone leaves the page open on a refresh loop.
    member = await _seed_user(db_session, "member_readonly", projects=(PROJECT,))
    await _seed_method_document(db_session)
    as_user(member)

    async with _client() as client:
        response = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    assert response.status_code == 200, response.text
    assert not (labs_roots / "workspaces").exists()


async def test_always_loaded_tokens_reports_what_the_stack_costs(
    db_session: AsyncSession, as_user
) -> None:
    member = await _seed_user(db_session, "member_budget", projects=(PROJECT,))
    await _seed_method_document(db_session)
    as_user(member)

    async with _client() as client:
        response = await client.get(f"/api/v1/guidance/projects/{PROJECT}/workspace")

    body = response.json()
    assert body["always_loaded_tokens"] > 0
    # It is a per-turn cost, so it must not grow with files the agent only reads
    # on demand: the mounted skill is far longer than a token count of zero, and
    # is not counted.
    assert any(file["always_loaded"] is False for file in body["files"])


# -- the document-routing endpoint -------------------------------------------


async def test_routing_covers_every_document_role(
    db_session: AsyncSession, as_user
) -> None:
    # Derived from `DOCUMENT_ROLES`, so a role added later appears here with no
    # edit to the route. The count is asserted as well as the set: a duplicate
    # entry would satisfy the set comparison alone.
    member = await _seed_user(db_session, "member_routing", projects=(PROJECT,))
    as_user(member)

    async with _client() as client:
        response = await client.get("/api/v1/guidance/document-routing")

    assert response.status_code == 200, response.text
    entries = response.json()
    assert len(entries) == len(DOCUMENT_ROLES)
    assert {entry["role"] for entry in entries} == set(DOCUMENT_ROLES)


async def test_destination_is_non_null_exactly_for_the_roles_that_materialize(
    db_session: AsyncSession, as_user
) -> None:
    member = await _seed_user(db_session, "member_destinations", projects=(PROJECT,))
    as_user(member)

    async with _client() as client:
        response = await client.get("/api/v1/guidance/document-routing")

    entries = response.json()
    for entry in entries:
        # One character: the question is whether the *role* mounts at all, which
        # is the gate independent of size.
        expected = should_materialize(entry["role"], 1)
        assert entry["materializes"] is expected, entry["role"]
        assert (entry["destination"] is not None) is expected, entry["role"]
        assert entry["always_loaded"] is False, entry["role"]
        assert entry["materialize_max_chars"] == MATERIALIZE_MAX_CHARS
        assert entry["description"]


async def test_routing_names_the_paths_the_workspace_actually_uses(
    db_session: AsyncSession, as_user
) -> None:
    # The two endpoints are only useful together: the routing table is a promise
    # about where an upload lands, and the workspace endpoint is where the user
    # sees it kept. A hardcoded string here would break that link silently.
    member = await _seed_user(db_session, "member_paths", projects=(PROJECT,))
    as_user(member)

    async with _client() as client:
        response = await client.get("/api/v1/guidance/document-routing")

    by_role = {entry["role"]: entry for entry in response.json()}
    assert by_role["guidance"]["kind"] == "method"
    assert by_role["guidance"]["destination"] == ".nexus/project/skills/<id>-<slug>/SKILL.md"
    assert by_role["policy"]["kind"] == "constraint"
    assert by_role["policy"]["destination"] == ".nexus/project/references/<id>-<slug>.md"
    assert by_role["source"]["kind"] == "evidence"
    assert by_role["source"]["destination"] is None
