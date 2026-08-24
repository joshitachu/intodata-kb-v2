"""Ownership binding + authorization for GitHub App installations.

Multi-tenant guard (Inspector Finding 1): the App key can mint a token for any of its
installations, so endpoints must not trust a client-supplied installation id. A user
becomes the owner of an installation only via the verified callback; all other endpoints
authorize against that binding (superadmin bypasses, matching project access checks).
"""

from __future__ import annotations

import logging

from sqlalchemy import select, text, update

from mcp_server.db.models import GithubAppInstallation, Project

logger = logging.getLogger(__name__)


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


async def installation_is_bound_to(db, installation_id: int, user_id: int) -> bool:
    """True only if this exact user already owns a verified binding.

    Deliberately no superadmin bypass: this answers "have we already proven this
    user controls this installation", which is what lets the callback skip a
    fresh OAuth round-trip. A bypass here would let the proof be skipped for a
    first-time binding, which is the thing the proof exists for.
    """
    binding = await db.get(GithubAppInstallation, installation_id)
    return binding is not None and int(binding.owner_user_id) == int(user_id)


async def authorize_installation(db, installation_id: int, user_id: int) -> bool:
    """True if user_id owns the installation binding, or is a superadmin."""
    if await _is_superadmin(db, user_id):
        return True
    binding = await db.get(GithubAppInstallation, installation_id)
    return binding is not None and int(binding.owner_user_id) == int(user_id)


async def prune_installation(db, installation_id: int) -> None:
    """Forget an installation that no longer exists on GitHub.

    Drops the ownership binding, unbinds every project that used it (so the
    project stops advertising an App connection it cannot authenticate), and
    clears the cached installation token. Caller commits.
    """
    from mcp_server.utils.github_app import forget_installation_token

    binding = await db.get(GithubAppInstallation, installation_id)
    if binding is not None:
        await db.delete(binding)
    await db.execute(
        update(Project)
        .where(Project.github_installation_id == installation_id)
        .values(connection_kind="pat", github_installation_id=None)
    )
    await forget_installation_token(installation_id)
    logger.info("Pruned GitHub App installation %s (no longer present on GitHub)", installation_id)


async def reconcile_installations(db, installations: list[GithubAppInstallation]) -> list[GithubAppInstallation]:
    """Return the installations that still exist on GitHub, pruning the rest.

    The binding table is written at connect time and never invalidated by
    GitHub, so an uninstall on GitHub's side leaves a row behind that makes the
    UI claim "Connected". Verify each id against GitHub on read and prune the
    ones GitHub answers 404 for. Installations we could not verify (network or
    App-config failure) are kept and returned: a transient failure must not
    delete state.
    """
    import httpx

    from mcp_server.utils.github_app import github_app_configured, installation_exists

    if not installations or not github_app_configured():
        return installations

    live: list[GithubAppInstallation] = []
    pruned = False
    async with httpx.AsyncClient(timeout=15.0) as client:
        for binding in installations:
            installation_id = int(binding.installation_id)
            try:
                exists = await installation_exists(installation_id, client=client)
            except Exception as exc:
                logger.warning(
                    "Could not verify GitHub App installation %s, keeping it: %s",
                    installation_id,
                    exc,
                )
                live.append(binding)
                continue
            if exists:
                live.append(binding)
            else:
                await prune_installation(db, installation_id)
                pruned = True
    if pruned:
        await db.commit()
    return live


async def get_owned_installation_ids(db, user_id: int) -> list[int]:
    """Installation ids a user owns (for listing/audit)."""
    result = await db.execute(
        select(GithubAppInstallation.installation_id).where(
            GithubAppInstallation.owner_user_id == user_id
        )
    )
    return [int(x) for x in result.scalars().all()]
