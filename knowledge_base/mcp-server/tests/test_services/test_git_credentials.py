"""Tests for the git credential-source Strategy and its plaintext-token git path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from mcp_server.services import git_credentials
from mcp_server.services import git_remote_service as service
from mcp_server.utils import encryption


@pytest.fixture
def fernet_key(monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "token_encryption_key", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _stub_mint(monkeypatch):
    async def fake_mint(installation_id):
        return f"ghs_installation_{installation_id}"

    monkeypatch.setattr(git_credentials, "mint_installation_token", fake_mint)


# --- provider selection -------------------------------------------------------

async def test_pat_project_uses_pat_provider(fernet_key):
    enc = encryption.encrypt_token("ghp_pat")
    project = SimpleNamespace(id="p1", github_token_enc=enc, github_installation_id=None, connection_kind="pat")
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.PatCredentialProvider)
    assert await provider.get_token() == "ghp_pat"


async def test_app_project_uses_app_provider():
    project = SimpleNamespace(id="p2", github_token_enc=None, github_installation_id=555, connection_kind="app")
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.GitHubAppCredentialProvider)
    assert await provider.get_token() == "ghs_installation_555"


async def test_installation_id_without_token_infers_app():
    project = SimpleNamespace(id="p3", github_token_enc=None, github_installation_id=9, connection_kind="")
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.GitHubAppCredentialProvider)


async def test_app_kind_wins_over_stored_pat(fernet_key):
    enc = encryption.encrypt_token("ghp_legacy")
    project = SimpleNamespace(id="p4", github_token_enc=enc, github_installation_id=1, connection_kind="app")
    token = await git_credentials.resolve_git_token(project)
    assert token == "ghs_installation_1"


async def test_pat_kind_wins_when_both_present(fernet_key):
    enc = encryption.encrypt_token("ghp_keep")
    project = SimpleNamespace(id="p5", github_token_enc=enc, github_installation_id=1, connection_kind="pat")
    token = await git_credentials.resolve_git_token(project)
    assert token == "ghp_keep"


async def test_no_credential_yields_none():
    project = SimpleNamespace(id="p6", github_token_enc=None, github_installation_id=None, connection_kind="")
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.NullCredentialProvider)
    assert await git_credentials.resolve_git_token(project) is None


# --- plaintext-token git path -------------------------------------------------

def test_authenticated_url_plaintext_strips_then_injects():
    # Installation tokens must use the x-access-token username, not token-as-username.
    tokenized = "https://old:cred@github.com/acme/repo.git"
    out = service.authenticated_url_plaintext(tokenized, "fresh_tok")
    assert out == "https://x-access-token:fresh_tok@github.com/acme/repo.git"


async def test_temporary_authenticated_origin_token_sets_and_restores():
    remote_url = "https://github.com/acme/repo.git"
    set_urls: list[str] = []

    async def git_func(local_path, *args, **kwargs):
        if args == ("remote", "get-url", "origin"):
            return remote_url
        if args[:3] == ("remote", "set-url", "origin"):
            set_urls.append(args[3])
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    async def op():
        return "ok"

    result = await service.with_temporary_authenticated_origin_token(
        "/repo", "minted_tok", op, git_func=git_func
    )
    assert result == "ok"
    # origin set to the tokenized URL, then restored to the clean URL last.
    assert any("minted_tok@github.com" in u for u in set_urls)
    assert set_urls[-1] == remote_url


async def test_fetch_remote_for_project_injects_resolved_token(fernet_key):
    enc = encryption.encrypt_token("resolved_pat")
    project = SimpleNamespace(
        id="p7", github_token_enc=enc, github_installation_id=None, connection_kind="pat"
    )
    remote_url = "https://github.com/acme/repo.git"
    set_urls: list[str] = []
    fetched = False

    async def git_func(local_path, *args, **kwargs):
        nonlocal fetched
        if args == ("remote", "get-url", "origin"):
            return remote_url
        if args[:3] == ("remote", "set-url", "origin"):
            set_urls.append(args[3])
            return ""
        if args[:1] == ("fetch",):
            fetched = True
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    await service.fetch_remote_for_project("/repo", project, git_func=git_func)
    assert fetched
    assert any("resolved_pat@github.com" in u for u in set_urls)
    assert set_urls[-1] == remote_url  # clean origin restored


# --- Azure DevOps connections -------------------------------------------------

async def test_azure_devops_project_uses_connection_provider():
    project = SimpleNamespace(
        id="ado1",
        github_token_enc=None,
        github_installation_id=None,
        azure_devops_connection_id="conn-1",
        connection_kind="azure_devops",
    )
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.AzureDevOpsCredentialProvider)


async def test_azure_devops_connection_id_without_kind_still_resolves():
    """A connection id and no stored PAT can only mean an Azure DevOps connection."""
    project = SimpleNamespace(
        id="ado2",
        github_token_enc=None,
        github_installation_id=None,
        azure_devops_connection_id="conn-2",
        connection_kind="",
    )
    assert isinstance(
        git_credentials.provider_for_project(project),
        git_credentials.AzureDevOpsCredentialProvider,
    )


async def test_azure_devops_kind_wins_over_stored_pat():
    project = SimpleNamespace(
        id="ado3",
        github_token_enc="enc",
        github_installation_id=7,
        azure_devops_connection_id="conn-3",
        connection_kind="azure_devops",
    )
    assert isinstance(
        git_credentials.provider_for_project(project),
        git_credentials.AzureDevOpsCredentialProvider,
    )


async def test_azure_devops_kind_without_connection_id_falls_back_to_pat(fernet_key):
    """A half-migrated row must degrade to the stored PAT, not crash the fetch."""
    enc = encryption.encrypt_token("ghp_pat")
    project = SimpleNamespace(
        id="ado4",
        github_token_enc=enc,
        github_installation_id=None,
        azure_devops_connection_id=None,
        connection_kind="azure_devops",
    )
    provider = git_credentials.provider_for_project(project)
    assert isinstance(provider, git_credentials.PatCredentialProvider)
    assert await provider.get_token() == "ghp_pat"


async def test_azure_devops_provider_decrypts_organization_pat(monkeypatch, fernet_key):
    """The organization PAT is looked up lazily, inside get_token()."""
    calls: list[str] = []

    async def fake_resolve_pat(db, connection_id):
        calls.append(connection_id)
        return "ado_pat"

    import mcp_server.services.azure_devops_connections as connections_mod

    monkeypatch.setattr(connections_mod, "resolve_pat", fake_resolve_pat)

    class _NullSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    import mcp_server.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", _NullSessionFactory())

    provider = git_credentials.AzureDevOpsCredentialProvider("conn-9")
    assert await provider.get_token() == "ado_pat"
    assert calls == ["conn-9"]


async def test_azure_devops_project_fetch_injects_organization_pat(monkeypatch):
    """End-to-end: an Azure DevOps project authenticates fetch with its org PAT."""

    class _StubProvider(git_credentials.GitCredentialProvider):
        async def get_token(self):
            return "ado_pat"

    monkeypatch.setattr(
        git_credentials, "AzureDevOpsCredentialProvider", lambda connection_id: _StubProvider()
    )

    project = SimpleNamespace(
        id="ado5",
        github_token_enc=None,
        github_installation_id=None,
        azure_devops_connection_id="conn-5",
        connection_kind="azure_devops",
    )
    remote_url = "https://dev.azure.com/org/Data/_git/etl"
    set_urls: list[str] = []
    fetched = False

    async def git_func(local_path, *args, **kwargs):
        nonlocal fetched
        if args == ("remote", "get-url", "origin"):
            return remote_url
        if args[:3] == ("remote", "set-url", "origin"):
            set_urls.append(args[3])
            return ""
        if args[:1] == ("fetch",):
            fetched = True
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    await service.fetch_remote_for_project("/repo", project, git_func=git_func)

    assert fetched
    # Azure DevOps authenticates on the password, so the PAT must land there.
    assert any("x-access-token:ado_pat@dev.azure.com" in url for url in set_urls)
    assert set_urls[-1] == remote_url  # clean origin restored
