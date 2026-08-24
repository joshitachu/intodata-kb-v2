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


async def test_callback_ignores_bad_state_without_attaching_a_project(client, app_configured, monkeypatch):
    """An unusable state is a lost project hint, not a dead end.

    GitHub returns users to the setup URL with no state at all, and our own state
    expires after 15 minutes of clicking around in GitHub's UI. Rejecting the
    callback stranded the user on ?connect_error=400. The state only ever named
    an optional project to attach, so an invalid one must attach nothing and
    otherwise proceed.
    """
    async def fake_account(installation_id):
        return "acme"

    monkeypatch.setattr(github_app, "get_installation_account_login", fake_account)

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 1, "state": "not-a-valid-jwt"},
    )
    assert resp.status_code == 200
    assert resp.json()["project_id"] is None


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


async def test_callback_requires_code_for_a_new_installation(client, app_configured, oauth_configured):
    """An unknown installation still needs proof of ownership."""
    connect = await client.get("/mcp/github/app/connect")
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]

    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 777, "state": state},
    )
    assert resp.status_code == 400


async def test_callback_accepts_setup_url_return_for_a_bound_installation(
    client, app_configured, oauth_configured, monkeypatch
):
    """Saving repository access on GitHub bounces here with no state and no code.

    Ownership was proven when the installation was first bound, so this return
    trip must succeed instead of dead-ending on ?connect_error=400.
    """
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
    first = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 888, "code": "oauth-code", "state": state},
    )
    assert first.status_code == 200

    # GitHub's post-update redirect: installation_id + setup_action only.
    again = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": 888, "setup_action": "update"},
    )
    assert again.status_code == 200
    assert again.json()["connected"] is True


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


async def _bind_installation(client, monkeypatch, installation_id: int, project_id: str | None = None):
    """Bind an installation to the test user via the verified callback flow."""

    async def fake_account(installation_id):
        return "acme"

    monkeypatch.setattr(github_app, "get_installation_account_login", fake_account)
    params = {"project_id": project_id} if project_id else {}
    connect = await client.get("/mcp/github/app/connect", params=params)
    state = parse_qs(urlparse(connect.json()["install_url"]).query)["state"][0]
    resp = await client.get(
        "/mcp/github/app/callback",
        params={"installation_id": installation_id, "setup_action": "install", "state": state},
    )
    assert resp.status_code == 200


async def test_installations_prunes_app_uninstalled_on_github(client, app_configured, monkeypatch):
    """Uninstalling the App on GitHub must clear the binding and the project link.

    GitHub never tells us about an uninstall, so a stale row kept the Sources
    page showing a green "Connected" card for an installation that can no longer
    mint a token.
    """
    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "gone-proj", "platform": "talend"})
    await _bind_installation(client, monkeypatch, 111, project_id="gone-proj")

    async def fake_exists(installation_id, client=None):
        return False

    monkeypatch.setattr(github_app, "installation_exists", fake_exists)

    resp = await client.get("/mcp/github/app/installations")
    assert resp.status_code == 200
    assert resp.json()["installations"] == []

    projects = (await client.get("/mcp/projects")).json()["projects"]
    proj = next(p for p in projects if p["id"] == "gone-proj")
    assert proj["connection_kind"] == "pat"
    assert proj.get("github_installation_id") in (None, 0)


async def test_installations_keeps_binding_when_github_unreachable(client, app_configured, monkeypatch):
    """A transient GitHub failure must never delete a live binding."""
    await _bind_installation(client, monkeypatch, 222)

    async def fake_exists(installation_id, client=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(github_app, "installation_exists", fake_exists)

    resp = await client.get("/mcp/github/app/installations")
    assert resp.status_code == 200
    assert [i["installation_id"] for i in resp.json()["installations"]] == [222]


async def test_installation_repos_404_when_installation_is_gone(client, app_configured, monkeypatch):
    await _bind_installation(client, monkeypatch, 333)

    async def fake_repos(installation_id):
        raise RuntimeError("HTTP 404")

    async def fake_exists(installation_id, client=None):
        return False

    monkeypatch.setattr(github_app, "list_installation_repositories", fake_repos)
    monkeypatch.setattr(github_app, "installation_exists", fake_exists)

    resp = await client.get("/mcp/github/app/installations/333/repos")
    assert resp.status_code == 404
    assert "no longer exists" in resp.json()["detail"]

    # The binding is pruned, so the entry-mode card stops showing "Connected".
    listing = await client.get("/mcp/github/app/installations")
    assert [i["installation_id"] for i in listing.json()["installations"]] == []


async def test_installation_repos_502_when_github_unreachable(client, app_configured, monkeypatch):
    await _bind_installation(client, monkeypatch, 444)

    async def fake_repos(installation_id):
        raise RuntimeError("timeout")

    async def fake_exists(installation_id, client=None):
        raise RuntimeError("timeout")

    monkeypatch.setattr(github_app, "list_installation_repositories", fake_repos)
    monkeypatch.setattr(github_app, "installation_exists", fake_exists)

    resp = await client.get("/mcp/github/app/installations/444/repos")
    assert resp.status_code == 502
