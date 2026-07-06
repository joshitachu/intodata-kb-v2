"""Tests for the GitHub App install-lifecycle routes."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from mcp_server.utils import github_app


@pytest.fixture
def app_configured(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_id", "424242")
    monkeypatch.setattr(config, "github_app_slug", "intodata-connector")
    monkeypatch.setattr(config, "github_app_private_key", "unused-in-route-tests")
    monkeypatch.setattr(config, "github_app_private_key_file", "")
    monkeypatch.setattr(config, "github_app_post_install_redirect", "")


async def test_connect_returns_install_url(client, app_configured):
    resp = await client.get("/mcp/github/app/connect")
    assert resp.status_code == 200
    url = resp.json()["install_url"]
    assert url.startswith("https://github.com/apps/intodata-connector/installations/new?")
    # State must be present and non-empty.
    state = parse_qs(urlparse(url).query)["state"][0]
    assert state


async def test_connect_503_when_unconfigured(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_id", "")
    monkeypatch.setattr(config, "github_app_slug", "")
    monkeypatch.setattr(config, "github_app_private_key", "")
    monkeypatch.setattr(config, "github_app_private_key_file", "")
    resp = await client.get("/mcp/github/app/connect")
    assert resp.status_code == 503


async def test_callback_attaches_installation_to_project(client, app_configured, monkeypatch):
    # A project to attach the installation to.
    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "app-proj", "platform": "talend"})

    async def fake_account(installation_id):
        return "acme-org"

    monkeypatch.setattr(github_app, "get_installation_account_login", fake_account)

    # Obtain a valid signed state via the connect endpoint.
    connect = await client.get("/mcp/github/app/connect", params={"project_id": "app-proj"})
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 987654, "setup_action": "install", "state": state},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["project_id"] == "app-proj"
    assert body["account_login"] == "acme-org"

    # Project now reflects the app connection.
    projects = (await client.get("/mcp/projects")).json()["projects"]
    proj = next(p for p in projects if p["id"] == "app-proj")
    assert proj["connection_kind"] == "app"
    assert proj["github_account_login"] == "acme-org"


async def test_callback_rejects_bad_state(client, app_configured):
    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 1, "state": "not-a-valid-jwt"},
    )
    assert resp.status_code == 400


@pytest.fixture
def oauth_configured(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_app_client_id", "Iv1.abc")
    monkeypatch.setattr(config, "github_app_client_secret", "secret")


async def test_callback_verifies_ownership_when_oauth_configured(client, app_configured, oauth_configured, monkeypatch):
    async def fake_exchange(code):
        return "gho_user_token"

    async def fake_can_access(token, installation_id):
        return True

    async def fake_account(installation_id):
        return "acme"

    monkeypatch.setattr(github_app, "exchange_user_access_token", fake_exchange)
    monkeypatch.setattr(github_app, "user_can_access_installation", fake_can_access)
    monkeypatch.setattr(github_app, "get_installation_account_login", fake_account)

    connect = await client.get("/mcp/github/app/connect")
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 555, "code": "oauth-code", "state": state},
    )
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


async def test_callback_rejects_when_user_lacks_installation(client, app_configured, oauth_configured, monkeypatch):
    async def fake_exchange(code):
        return "gho_user_token"

    async def fake_can_access(token, installation_id):
        return False

    monkeypatch.setattr(github_app, "exchange_user_access_token", fake_exchange)
    monkeypatch.setattr(github_app, "user_can_access_installation", fake_can_access)

    connect = await client.get("/mcp/github/app/connect")
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 666, "code": "oauth-code", "state": state},
    )
    assert resp.status_code == 403


async def test_callback_requires_code_when_oauth_configured(client, app_configured, oauth_configured):
    connect = await client.get("/mcp/github/app/connect")
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 777, "state": state},
    )
    assert resp.status_code == 400


async def test_installation_repos_lists(client, app_configured, monkeypatch):
    async def fake_repos(installation_id):
        return [
            {"full_name": "acme/repo", "clone_url": "https://github.com/acme/repo.git", "private": True, "default_branch": "main"},
        ]

    async def fake_account(installation_id):
        return "acme"

    monkeypatch.setattr(github_app, "list_installation_repositories", fake_repos)
    monkeypatch.setattr(github_app, "get_installation_account_login", fake_account)

    resp = await client.get("/mcp/github/app/installations/321/repos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installation_id"] == 321
    assert body["account_login"] == "acme"
    assert body["repositories"][0]["full_name"] == "acme/repo"
