"""Tests for GitHub App installation ownership binding + authorization."""

from __future__ import annotations

from sqlalchemy import text

from mcp_server.services.github_app_ownership import (
    authorize_installation,
    get_owned_installation_ids,
    record_installation_owner,
)


async def _seed_user(db, user_id: int, is_superadmin: int) -> None:
    await db.execute(
        text("INSERT INTO users (id, username, is_superadmin) VALUES (:id, :u, :s)"),
        {"id": user_id, "u": f"user{user_id}", "s": is_superadmin},
    )
    await db.commit()


async def test_owner_is_authorized(db_session):
    await _seed_user(db_session, 2, 0)
    await record_installation_owner(db_session, 100, owner_user_id=2, account_login="acme")
    await db_session.commit()

    assert await authorize_installation(db_session, 100, 2) is True


async def test_non_owner_is_rejected(db_session):
    await _seed_user(db_session, 2, 0)
    await _seed_user(db_session, 3, 0)
    await record_installation_owner(db_session, 101, owner_user_id=2, account_login=None)
    await db_session.commit()

    assert await authorize_installation(db_session, 101, 3) is False


async def test_superadmin_bypasses_binding(db_session):
    # conftest seeds user 1 as superadmin; no binding exists for this installation.
    assert await authorize_installation(db_session, 999, 1) is True


async def test_unbound_installation_is_rejected_for_regular_user(db_session):
    await _seed_user(db_session, 2, 0)
    assert await authorize_installation(db_session, 12345, 2) is False


async def test_record_is_first_writer_wins(db_session):
    await _seed_user(db_session, 2, 0)
    await _seed_user(db_session, 3, 0)
    await record_installation_owner(db_session, 200, owner_user_id=2, account_login="acme")
    await db_session.commit()
    # A second record call must not steal ownership; only refresh login.
    await record_installation_owner(db_session, 200, owner_user_id=3, account_login="acme-renamed")
    await db_session.commit()

    assert await authorize_installation(db_session, 200, 2) is True
    assert await authorize_installation(db_session, 200, 3) is False
    assert await get_owned_installation_ids(db_session, 2) == [200]
