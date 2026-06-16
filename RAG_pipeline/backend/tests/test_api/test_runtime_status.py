"""Tests for runtime status API endpoint (src/api/routes/runtime.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Role, User


def _mock_user(id: int = 1):
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = id
    user.roles = [role]
    return user


@pytest.fixture
def mock_deps():
    from src.api.dependencies import get_current_user, get_db

    mock_db = AsyncMock()
    user = _mock_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "user": user}
    app.dependency_overrides.clear()


class TestGetRuntimeStatus:
    async def test_no_provider_configured_returns_disabled(self, mock_deps):
        with patch("src.runtime.status.settings") as mock_settings:
            mock_settings.runtime_provider_key = ""
            mock_settings.runtime_provider_label = "Live runtime"

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "disabled"
        assert data["provider_key"] is None

    async def test_provider_without_health_url_returns_unavailable(self, mock_deps):
        with patch("src.runtime.status.settings") as mock_settings:
            mock_settings.runtime_provider_key = "talend_tmc"
            mock_settings.runtime_provider_label = "Talend TMC"
            mock_settings.runtime_provider_health_url = ""

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unavailable"
        assert data["provider_key"] == "talend_tmc"

    async def test_health_url_2xx_returns_connected(self, mock_deps):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("src.runtime.status.settings") as mock_settings, \
                patch("httpx.AsyncClient") as mock_client_cls:
            mock_settings.runtime_provider_key = "talend_tmc"
            mock_settings.runtime_provider_label = "Talend TMC"
            mock_settings.runtime_provider_health_url = "http://tmc.example.com/health"
            mock_settings.runtime_status_timeout = 5.0

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"

    async def test_health_url_failure_returns_unavailable(self, mock_deps):
        with patch("src.runtime.status.settings") as mock_settings, \
                patch("httpx.AsyncClient") as mock_client_cls:
            mock_settings.runtime_provider_key = "talend_tmc"
            mock_settings.runtime_provider_label = "Talend TMC"
            mock_settings.runtime_provider_health_url = "http://tmc.example.com/health"
            mock_settings.runtime_status_timeout = 5.0

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unavailable"

    async def test_requires_authentication(self):
        """Without an access_token cookie, the endpoint returns 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/runtime/status")

        assert resp.status_code == 401
