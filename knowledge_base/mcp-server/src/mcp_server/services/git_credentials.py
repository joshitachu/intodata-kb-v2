"""Strategy for resolving a git access token from a project's connection.

A repo connection has a credential source. Three exist:

- ``pat``           -> a Personal Access Token, Fernet-encrypted at rest on the
                       project row (legacy/fallback).
- ``app``           -> a GitHub App installation; a short-lived token is minted on
                       demand.
- ``azure_devops``  -> an Azure DevOps organization connection; the organization's
                       Fernet-encrypted PAT is shared by every project cloned from
                       that organization.

All three resolve to a single plaintext token that the git layer injects into the
clone URL. This is the one resolution point (CLAUDE.md #10) — callers ask
``provider_for_project(project)`` / ``resolve_git_token(project)`` and never branch
on connection kind themselves.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from mcp_server.utils.encryption import decrypt_token
from mcp_server.utils.github_app import mint_installation_token

logger = logging.getLogger(__name__)

CONNECTION_KIND_PAT = "pat"
CONNECTION_KIND_APP = "app"
CONNECTION_KIND_AZURE_DEVOPS = "azure_devops"


class GitCredentialProvider(ABC):
    """Yields a plaintext git token, or None when the connection carries no credential."""

    @abstractmethod
    async def get_token(self) -> str | None: ...


class NullCredentialProvider(GitCredentialProvider):
    """No credential (e.g. public repo or local/zip source)."""

    async def get_token(self) -> str | None:
        return None


class PatCredentialProvider(GitCredentialProvider):
    """Decrypts a stored PAT."""

    def __init__(self, token_enc: str) -> None:
        self._token_enc = token_enc

    async def get_token(self) -> str | None:
        return decrypt_token(self._token_enc)


class GitHubAppCredentialProvider(GitCredentialProvider):
    """Mints a short-lived installation token for a GitHub App installation."""

    def __init__(self, installation_id: int) -> None:
        self._installation_id = installation_id

    async def get_token(self) -> str | None:
        return await mint_installation_token(self._installation_id)


class AzureDevOpsCredentialProvider(GitCredentialProvider):
    """Decrypts the organization PAT held by an Azure DevOps connection.

    The lookup happens inside ``get_token`` (not at construction) so selecting a
    strategy stays synchronous and free of database access, matching the other
    providers.
    """

    def __init__(self, connection_id: str) -> None:
        self._connection_id = connection_id

    async def get_token(self) -> str | None:
        from mcp_server.db.session import async_session_factory
        from mcp_server.services.azure_devops_connections import resolve_pat

        async with async_session_factory() as db:
            return await resolve_pat(db, self._connection_id)


def provider_for_project(project) -> GitCredentialProvider:
    """Select the credential strategy for *project*.

    Precedence follows how explicit the binding is: an Azure DevOps connection or a
    GitHub App installation wins when marked (or when its reference is present
    without a stored PAT); otherwise a stored PAT is used. Reads attributes
    defensively so this works before and after schema migrations.
    """
    connection_kind = str(getattr(project, "connection_kind", "") or "")
    installation_id = getattr(project, "github_installation_id", None)
    azure_connection_id = getattr(project, "azure_devops_connection_id", None)
    token_enc = getattr(project, "github_token_enc", None)

    if connection_kind == CONNECTION_KIND_AZURE_DEVOPS or (azure_connection_id and not token_enc):
        if azure_connection_id:
            return AzureDevOpsCredentialProvider(str(azure_connection_id))
        logger.warning(
            "Project %s marked Azure DevOps-connected but has no connection id",
            getattr(project, "id", "?"),
        )
    if connection_kind == CONNECTION_KIND_APP or (installation_id and not token_enc):
        if installation_id:
            return GitHubAppCredentialProvider(int(installation_id))
        logger.warning(
            "Project %s marked app-connected but has no installation id",
            getattr(project, "id", "?"),
        )
    if token_enc:
        return PatCredentialProvider(token_enc)
    return NullCredentialProvider()


async def resolve_git_token(project) -> str | None:
    """Resolve a plaintext git token for *project* via its credential strategy."""
    return await provider_for_project(project).get_token()
