"""Slice 3: superadmin-only /admin/model-config endpoint (real DB)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.main import app
from src.api.dependencies import get_current_user, get_db, get_qdrant
from src.db.models import User
from src.generation import model_config as mc

CSRF = {"cookie": "csrf_token=t", "X-CSRF-Token": "t"}


@pytest.fixture(autouse=True)
def _reset_override():
    mc.clear_override()
    yield
    mc.clear_override()


async def _seed(db: AsyncSession, username: str, superadmin: bool) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        is_superadmin=superadmin,
        mfa_enabled=True,
    )
    db.add(user)
    await db.flush()
    return user


def _override(db: AsyncSession, user: User) -> None:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_qdrant] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: user


def _audit():
    m = MagicMock()
    m.return_value.log = AsyncMock()
    return patch("src.api.routes.admin.AuditService", m)


async def test_superadmin_sets_and_reads_model_standard(db_session: AsyncSession) -> None:
    root = await _seed(db_session, "root_mc", superadmin=True)
    _override(db_session, root)
    try:
        with _audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                put = await client.put(
                    "/api/v1/admin/model-config",
                    json={"provider": "ollama", "model": "gemma3:12b"},
                    headers=CSRF,
                )
                get = await client.get("/api/v1/admin/model-config")
        assert put.status_code == 200, put.text
        assert mc.get_override() == {"provider": "ollama", "model": "gemma3:12b"}
        body = get.json()
        assert body["provider"] == "ollama"
        assert body["model"] == "gemma3:12b"
        assert body["effective_chain"][0] == "ollama"
    finally:
        app.dependency_overrides.clear()


async def test_disallowed_model_rejected(db_session: AsyncSession) -> None:
    root = await _seed(db_session, "root_mc2", superadmin=True)
    _override(db_session, root)
    try:
        with _audit():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/admin/model-config",
                    json={"provider": "ollama", "model": "bogus-model"},
                    headers=CSRF,
                )
        assert resp.status_code == 400
        assert mc.get_override() is None
    finally:
        app.dependency_overrides.clear()


async def test_non_superadmin_cannot_set_model(db_session: AsyncSession) -> None:
    admin = await _seed(db_session, "admin_mc", superadmin=False)
    _override(db_session, admin)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/admin/model-config",
                json={"provider": "ollama", "model": "gemma3:12b"},
                headers=CSRF,
            )
        assert resp.status_code == 403
        assert mc.get_override() is None
    finally:
        app.dependency_overrides.clear()
