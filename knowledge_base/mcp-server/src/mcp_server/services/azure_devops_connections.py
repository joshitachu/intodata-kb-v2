"""Ownership binding + lifecycle for Azure DevOps organization connections.

Mirrors :mod:`mcp_server.services.github_app_ownership`: a connection is owned by
the user who created it, and every endpoint that names a connection id authorizes
against that binding (superadmin bypasses, matching project access checks).
Connection ids are UUIDs rather than enumerable integers, but authorization does
not rely on that.

The organization PAT is verified against Azure DevOps *before* it is persisted, so
a stored connection always represents a credential that worked at least once.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select, text, update

from mcp_server.db.models import AzureDevOpsConnection, Project
from mcp_server.utils.azure_devops_api import normalize_organization_url, verify_connection
from mcp_server.utils.encryption import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)


async def _is_superadmin(db, user_id: int) -> bool:
    row = await db.execute(
        text("SELECT is_superadmin FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    return bool(row.scalar())


async def create_or_update_connection(
    db,
    *,
    owner_user_id: int,
    organization_url: str,
    pat: str,
) -> AzureDevOpsConnection:
    """Verify a PAT against its organization, then store it encrypted.

    Re-connecting the same organization as the same user rotates the token on the
    existing row instead of creating a duplicate, so projects already bound to the
    connection keep working after a PAT expires. Caller commits.

    Raises :class:`AzureDevOpsError` (credential/URL rejected or Azure DevOps
    unreachable) and ``ValueError`` (unusable URL) without persisting anything.
    """
    normalized_url, organization_name = normalize_organization_url(organization_url)
    account_name = await verify_connection(normalized_url, pat)
    pat_enc = encrypt_token(pat)
    now = datetime.datetime.now(datetime.timezone.utc)

    result = await db.execute(
        select(AzureDevOpsConnection).where(
            AzureDevOpsConnection.owner_user_id == owner_user_id,
            AzureDevOpsConnection.organization_url == normalized_url,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.pat_enc = pat_enc
        existing.organization_name = organization_name
        existing.account_name = account_name
        existing.last_verified_at = now
        logger.info(
            "Rotated Azure DevOps PAT for connection %s (%s)", existing.id, normalized_url
        )
        return existing

    connection = AzureDevOpsConnection(
        id=str(uuid.uuid4()),
        organization_url=normalized_url,
        organization_name=organization_name,
        account_name=account_name,
        pat_enc=pat_enc,
        owner_user_id=owner_user_id,
        last_verified_at=now,
    )
    db.add(connection)
    logger.info("Connected Azure DevOps organization %s for user %s", normalized_url, owner_user_id)
    return connection


async def list_connections(db, user_id: int) -> list[AzureDevOpsConnection]:
    """Connections visible to *user_id* (all of them for a superadmin)."""
    query = select(AzureDevOpsConnection)
    if not await _is_superadmin(db, user_id):
        query = query.where(AzureDevOpsConnection.owner_user_id == user_id)
    result = await db.execute(query.order_by(AzureDevOpsConnection.organization_name))
    return list(result.scalars().all())


async def get_connection(db, connection_id: str) -> AzureDevOpsConnection | None:
    return await db.get(AzureDevOpsConnection, connection_id)


async def authorize_connection(db, connection_id: str, user_id: int) -> bool:
    """True if *user_id* owns the connection, or is a superadmin."""
    if await _is_superadmin(db, user_id):
        return True
    connection = await db.get(AzureDevOpsConnection, connection_id)
    return connection is not None and int(connection.owner_user_id) == int(user_id)


async def delete_connection(db, connection_id: str) -> bool:
    """Forget a connection and unbind every project that used it. Caller commits.

    Projects are reverted to ``connection_kind='pat'`` with no stored token, which
    is the honest state: they have a remote URL but no way to authenticate, and the
    readiness report will say so rather than claiming a live connection.
    """
    connection = await db.get(AzureDevOpsConnection, connection_id)
    if connection is None:
        return False
    await db.execute(
        update(Project)
        .where(Project.azure_devops_connection_id == connection_id)
        .values(connection_kind="pat", azure_devops_connection_id=None)
    )
    await db.delete(connection)
    logger.info("Deleted Azure DevOps connection %s", connection_id)
    return True


async def resolve_pat(db, connection_id: str) -> str | None:
    """Decrypt the stored PAT for *connection_id*, or None when unavailable."""
    connection = await db.get(AzureDevOpsConnection, connection_id)
    if connection is None or not connection.pat_enc:
        logger.warning("No Azure DevOps connection credential for %s", connection_id)
        return None
    try:
        return decrypt_token(connection.pat_enc)
    except Exception as exc:  # key rotation / corrupt ciphertext
        logger.error("Could not decrypt Azure DevOps PAT for %s: %s", connection_id, exc)
        return None


async def touch_verified(db, connection_id: str) -> None:
    """Record a successful live verification. Caller commits."""
    await db.execute(
        update(AzureDevOpsConnection)
        .where(AzureDevOpsConnection.id == connection_id)
        .values(last_verified_at=datetime.datetime.now(datetime.timezone.utc))
    )
