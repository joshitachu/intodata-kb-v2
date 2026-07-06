"""Ownership binding + authorization for GitHub App installations.

Multi-tenant guard (Inspector Finding 1): the App key can mint a token for any of its
installations, so endpoints must not trust a client-supplied installation id. A user
becomes the owner of an installation only via the verified callback; all other endpoints
authorize against that binding (superadmin bypasses, matching project access checks).
"""

from __future__ import annotations

from sqlalchemy import select, text

from mcp_server.db.models import GithubAppInstallation


async def _is_superadmin(db, user_id: int) -> bool:
    row = await db.execute(
        text("SELECT is_superadmin FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    value = row.scalar()
    return bool(value)


async def record_installation_owner(
    db,
    installation_id: int,
    owner_user_id: int,
    account_login: str | None,
) -> None:
    """Upsert the ownership binding for an installation (first verified connector wins)."""
    existing = await db.get(GithubAppInstallation, installation_id)
    if existing is None:
        db.add(
            GithubAppInstallation(
                installation_id=installation_id,
                owner_user_id=owner_user_id,
                account_login=account_login,
            )
        )
    else:
        # Keep the original owner; only refresh the display login.
        if account_login:
            existing.account_login = account_login


async def authorize_installation(db, installation_id: int, user_id: int) -> bool:
    """True if user_id owns the installation binding, or is a superadmin."""
    if await _is_superadmin(db, user_id):
        return True
    binding = await db.get(GithubAppInstallation, installation_id)
    return binding is not None and int(binding.owner_user_id) == int(user_id)


async def get_owned_installation_ids(db, user_id: int) -> list[int]:
    """Installation ids a user owns (for listing/audit)."""
    result = await db.execute(
        select(GithubAppInstallation.installation_id).where(
            GithubAppInstallation.owner_user_id == user_id
        )
    )
    return [int(x) for x in result.scalars().all()]
