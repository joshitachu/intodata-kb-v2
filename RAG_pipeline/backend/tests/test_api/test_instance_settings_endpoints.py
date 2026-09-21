"""Tests for the RAG-side /admin/instance-settings endpoints.

Follows the established mock-based pattern in test_admin.py so we don't need
a running database. The service layer (test_instance_settings_service.py) has
the round-trip tests against a real in-memory SQLite engine.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _make_superadmin():
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.is_active = True
    user.is_superadmin = True
    return user


def _make_regular_user():
    user = MagicMock()
    user.id = 2
    user.username = "dev"
    user.is_active = True
    user.is_superadmin = False
    return user


@pytest.fixture
def admin_deps():
    from src.api.dependencies import get_current_user, get_db

    mock_db = AsyncMock()
    user = _make_superadmin()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: user
    yield {"db": mock_db, "user": user}
    app.dependency_overrides.clear()


@pytest.fixture
def regular_deps():
    from src.api.dependencies import get_current_user, get_db

    mock_db = AsyncMock()
    user = _make_regular_user()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: user
    yield {"db": mock_db, "user": user}
    app.dependency_overrides.clear()


def _empty_settings_result():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


class TestGet:
    async def test_get_requires_superadmin(self, regular_deps):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/instance-settings")
        assert resp.status_code == 403

    async def test_get_returns_empty_when_unset(self, admin_deps):
        admin_deps["db"].execute = AsyncMock(return_value=_empty_settings_result())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/instance-settings")
        assert resp.status_code == 200
        assert resp.json() == {"settings": {}}

    async def test_get_returns_overrides(self, admin_deps):
        row1 = MagicMock()
        row1.key = "rag_chat_model"
        row1.value = "anthropic/claude-sonnet-4-6"
        row2 = MagicMock()
        row2.key = "doc_gen_write_model"
        row2.value = "anthropic/claude-sonnet-4-6"
        result = MagicMock()
        result.scalars.return_value.all.return_value = [row1, row2]
        admin_deps["db"].execute = AsyncMock(return_value=result)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/instance-settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["settings"]["rag_chat_model"] == "anthropic/claude-sonnet-4-6"
        assert body["settings"]["doc_gen_write_model"] == "anthropic/claude-sonnet-4-6"


class TestPut:
    async def test_put_rejects_unknown_key(self, admin_deps):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/admin/instance-settings",
                json={"settings": {"not_a_real_key": "x"}},
                headers=CSRF_HEADERS,
            )
        assert resp.status_code == 400
        assert "Unknown setting key" in resp.text

    async def test_put_writes_and_audits(self, admin_deps):
        # The route calls db.execute() three times:
        # 1. get_all() before
        # 2. delete() inside set_many()
        # 3. get_all() after
        empty = _empty_settings_result()
        row = MagicMock()
        row.key = "rag_chat_model"
        row.value = "anthropic/claude-sonnet-4-6"
        new_state = MagicMock()
        new_state.scalars.return_value.all.return_value = [row]
        admin_deps["db"].execute = AsyncMock(side_effect=[empty, empty, new_state])
        admin_deps["db"].flush = AsyncMock()
        admin_deps["db"].commit = AsyncMock()

        captured_audit = {}

        async def fake_audit_log(**kwargs):
            captured_audit.update(kwargs)
            return MagicMock()

        with patch("src.api.routes.instance_settings.AuditService") as mock_audit_cls:
            mock_audit = mock_audit_cls.return_value
            mock_audit.log = fake_audit_log

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/admin/instance-settings",
                    json={"settings": {"rag_chat_model": "anthropic/claude-sonnet-4-6"}},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert captured_audit["action"] == "update_instance_settings"
        assert captured_audit["username"] == "admin"
        assert "rag_chat_model" in captured_audit["details"]["diff"]
        assert captured_audit["details"]["diff"]["rag_chat_model"]["after"] == "anthropic/claude-sonnet-4-6"

    async def test_put_clears_on_empty_string(self, admin_deps):
        admin_deps["db"].execute = AsyncMock(
            side_effect=[
                _empty_settings_result(),
                _empty_settings_result(),
                _empty_settings_result(),
            ]
        )
        admin_deps["db"].flush = AsyncMock()
        admin_deps["db"].commit = AsyncMock()

        with patch("src.api.routes.instance_settings.AuditService") as mock_audit_cls:
            mock_audit_cls.return_value.log = AsyncMock()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/v1/admin/instance-settings",
                    json={"settings": {"rag_chat_model": ""}},
                    headers=CSRF_HEADERS,
                )
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}