"""Tests for the Azure DevOps connection routes and their ownership guard."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from mcp_server.utils import azure_devops_api as ado


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "token_encryption_key", Fernet.generate_key().decode())


@pytest.fixture
def azure_ok(monkeypatch):
    """Azure DevOps accepts the token and reports two repositories."""

    async def fake_verify(organization_url, pat):
        return "Ada L"

    async def fake_repos(organization_url, pat):
        return [
            {
                "id": "a1",
                "name": "etl",
                "project": "Data",
                "clone_url": f"{organization_url}/Data/_git/etl",
                "web_url": None,
                "default_branch": "main",
                "is_disabled": False,
                "size_bytes": 10,
            },
            {
                "id": "b2",
                "name": "api",
                "project": "Platform",
                "clone_url": f"{organization_url}/Platform/_git/api",
                "web_url": None,
                "default_branch": "main",
                "is_disabled": False,
                "size_bytes": 20,
            },
        ]

    import mcp_server.api.routes.azure_devops as routes
    import mcp_server.services.azure_devops_connections as connections

    monkeypatch.setattr(routes, "verify_connection", fake_verify)
    monkeypatch.setattr(routes, "list_repositories", fake_repos)
    monkeypatch.setattr(connections, "verify_connection", fake_verify)


async def _connect(client, org_url="https://dev.azure.com/my-org", pat="pat-1"):
    return await client.post(
        "/mcp/azure-devops/connections",
        json={"organization_url": org_url, "pat": pat},
    )


async def test_connect_normalizes_url_and_never_returns_the_token(client, azure_ok):
    resp = await _connect(client, "https://dev.azure.com/my-org/Data/_git/etl?path=/x")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_url"] == "https://dev.azure.com/my-org"
    assert body["organization_name"] == "my-org"
    assert body["account_name"] == "Ada L"
    assert body["id"]
    assert "pat" not in body and "pat_enc" not in body


async def test_connect_stores_the_token_encrypted(client, azure_ok, session_factory):
    resp = await _connect(client)
    connection_id = resp.json()["id"]

    async with session_factory() as db:
        row = (
            await db.execute(
                text("SELECT pat_enc FROM azure_devops_connections WHERE id = :cid"),
                {"cid": connection_id},
            )
        ).scalar_one()
    assert "pat-1" not in row

    from mcp_server.utils.encryption import decrypt_token

    assert decrypt_token(row) == "pat-1"


async def test_connect_rejects_an_unusable_url(client, azure_ok):
    resp = await _connect(client, "https://dev.azure.com")
    assert resp.status_code == 400
    assert "organization" in resp.json()["detail"].lower()


async def test_connect_surfaces_a_rejected_token_as_401(client, monkeypatch):
    async def fake_verify(organization_url, pat):
        raise ado.AzureDevOpsError("bad token", status=401)

    import mcp_server.services.azure_devops_connections as connections

    monkeypatch.setattr(connections, "verify_connection", fake_verify)

    resp = await _connect(client)
    assert resp.status_code == 401


async def test_connect_surfaces_an_unreachable_azure_devops_as_502(client, monkeypatch):
    """A transient outage must not read to the user as "your token is wrong"."""

    async def fake_verify(organization_url, pat):
        raise ado.AzureDevOpsError("unreachable", retryable=True)

    import mcp_server.services.azure_devops_connections as connections

    monkeypatch.setattr(connections, "verify_connection", fake_verify)

    resp = await _connect(client)
    assert resp.status_code == 502


async def test_unverified_token_is_never_persisted(client, monkeypatch, session_factory):
    async def fake_verify(organization_url, pat):
        raise ado.AzureDevOpsError("bad token", status=401)

    import mcp_server.services.azure_devops_connections as connections

    monkeypatch.setattr(connections, "verify_connection", fake_verify)
    await _connect(client)

    async with session_factory() as db:
        count = (
            await db.execute(text("SELECT COUNT(*) FROM azure_devops_connections"))
        ).scalar_one()
    assert count == 0


async def test_reconnecting_the_same_organization_rotates_instead_of_duplicating(
    client, azure_ok, session_factory
):
    first = (await _connect(client, pat="pat-old")).json()
    second = (await _connect(client, pat="pat-new")).json()
    assert first["id"] == second["id"]

    async with session_factory() as db:
        rows = (
            await db.execute(text("SELECT pat_enc FROM azure_devops_connections"))
        ).scalars().all()
    assert len(rows) == 1

    from mcp_server.utils.encryption import decrypt_token

    assert decrypt_token(rows[0]) == "pat-new"


async def test_list_connections(client, azure_ok):
    await _connect(client)
    resp = await client.get("/mcp/azure-devops/connections")
    assert resp.status_code == 200
    connections = resp.json()["connections"]
    assert [c["organization_name"] for c in connections] == ["my-org"]


async def test_list_repos_returns_the_picker_list(client, azure_ok):
    connection_id = (await _connect(client)).json()["id"]
    resp = await client.get(f"/mcp/azure-devops/connections/{connection_id}/repos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_name"] == "my-org"
    assert [r["name"] for r in body["repositories"]] == ["etl", "api"]
    assert body["repositories"][0]["clone_url"] == "https://dev.azure.com/my-org/Data/_git/etl"


async def test_repos_403_for_a_connection_owned_by_another_user(
    client, azure_ok, session_factory
):
    """The connection id comes from the client, so ownership must be enforced."""
    async with session_factory() as db:
        await db.execute(
            text("UPDATE users SET is_superadmin = 0 WHERE id = 1"),
        )
        await db.execute(
            text(
                "INSERT INTO azure_devops_connections "
                "(id, organization_url, organization_name, pat_enc, owner_user_id, created_at, updated_at) "
                "VALUES ('other-conn', 'https://dev.azure.com/other', 'other', 'x', 99, NOW(), NOW())"
            )
        )
        await db.commit()

    resp = await client.get("/mcp/azure-devops/connections/other-conn/repos")
    assert resp.status_code == 403


async def test_repos_403_for_an_unknown_connection_id(client, azure_ok, session_factory):
    """A 404 here would confirm which connection ids exist."""
    async with session_factory() as db:
        await db.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
        await db.commit()

    resp = await client.get("/mcp/azure-devops/connections/does-not-exist/repos")
    assert resp.status_code == 403


async def test_test_endpoint_reverifies_the_stored_token(client, azure_ok):
    connection_id = (await _connect(client)).json()["id"]
    resp = await client.post(f"/mcp/azure-devops/connections/{connection_id}/test")
    assert resp.status_code == 200
    assert resp.json()["account_name"] == "Ada L"


async def test_test_endpoint_reports_an_expired_token(client, azure_ok, monkeypatch):
    connection_id = (await _connect(client)).json()["id"]

    async def fake_verify(organization_url, pat):
        raise ado.AzureDevOpsError("expired", status=401)

    import mcp_server.api.routes.azure_devops as routes

    monkeypatch.setattr(routes, "verify_connection", fake_verify)

    resp = await client.post(f"/mcp/azure-devops/connections/{connection_id}/test")
    assert resp.status_code == 401


async def test_delete_unbinds_projects_that_used_the_connection(
    client, azure_ok, session_factory
):
    connection_id = (await _connect(client)).json()["id"]
    await client.post(
        "/mcp/add-repo", json={"url": "zip", "project_id": "ado-proj", "platform": "talend"}
    )
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE projects SET connection_kind = 'azure_devops', "
                "azure_devops_connection_id = :cid WHERE id = 'ado-proj'"
            ),
            {"cid": connection_id},
        )
        await db.commit()

    resp = await client.delete(f"/mcp/azure-devops/connections/{connection_id}")
    assert resp.status_code == 200

    async with session_factory() as db:
        kind, bound = (
            await db.execute(
                text(
                    "SELECT connection_kind, azure_devops_connection_id "
                    "FROM projects WHERE id = 'ado-proj'"
                )
            )
        ).one()
    assert kind == "pat"
    assert bound is None


async def test_add_repo_rejects_a_connection_the_caller_does_not_own(
    client, azure_ok, session_factory
):
    async with session_factory() as db:
        await db.execute(text("UPDATE users SET is_superadmin = 0 WHERE id = 1"))
        await db.execute(
            text(
                "INSERT INTO azure_devops_connections "
                "(id, organization_url, organization_name, pat_enc, owner_user_id, created_at, updated_at) "
                "VALUES ('foreign', 'https://dev.azure.com/other', 'other', 'x', 99, NOW(), NOW())"
            )
        )
        await db.commit()

    resp = await client.post(
        "/mcp/add-repo",
        json={
            "url": "https://dev.azure.com/other/Data/_git/etl",
            "project_id": "stolen",
            "platform": "talend",
            "azure_devops_connection_id": "foreign",
        },
    )
    assert resp.status_code == 403


async def test_add_repo_binds_the_connection_and_normalizes_a_browser_url(
    client, azure_ok, session_factory
):
    """A pasted browser URL registers as a bare clone URL bound to the connection."""
    from mcp_server.config import config

    connection_id = (await _connect(client)).json()["id"]
    # Pre-create the clone directory so add-repo skips the real `git clone`.
    (config.repos_dir / "ado-etl").mkdir(parents=True, exist_ok=True)

    resp = await client.post(
        "/mcp/add-repo",
        json={
            "url": "https://my-org@dev.azure.com/my-org/Data%20Platform/_git/etl?path=/x&version=GBmain",
            "project_id": "ado-etl",
            "platform": "talend",
            "azure_devops_connection_id": connection_id,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_type"] == "git"
    assert body["repo_url"] == "https://dev.azure.com/my-org/Data%20Platform/_git/etl"
    assert body["connection_kind"] == "azure_devops"
    assert body["azure_devops_connection_id"] == connection_id
    # The organization label is shown instead of a GitHub account login.
    assert body["github_account_login"] == "my-org"
    # A credential is resolvable even though no PAT is stored on the project row.
    assert body["has_github_token"] is True
