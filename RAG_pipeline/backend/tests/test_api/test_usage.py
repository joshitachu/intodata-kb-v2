"""Tests for usage recording API endpoint (src/api/routes/usage.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.fixture
def mock_deps():
    """Override dependencies for usage endpoint tests."""
    from src.api.dependencies import get_db

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    yield {"db": mock_db}
    app.dependency_overrides.clear()


class TestRecordUsage:
    async def test_record_usage_success(self, mock_deps):
        """POST /usage/record with valid data creates entry."""
        mock_db = mock_deps["db"]

        # Mock the added object to get an id
        def capture_add(obj):
            obj.id = 42

        mock_db.add = capture_add

        with patch("src.api.routes.usage.settings") as mock_settings:
            mock_settings.mcp_ingest_api_key = ""  # No auth required

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/usage/record",
                    json={
                        "interface": "doc_generation",
                        "model_name": "claude",
                        "provider": "anthropic",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "cost_usd": 0.02,
                        "project_id": "test-project",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["id"] == 42

    async def test_record_usage_requires_interface(self, mock_deps):
        """POST /usage/record without interface field fails validation."""
        with patch("src.api.routes.usage.settings") as mock_settings:
            mock_settings.mcp_ingest_api_key = ""

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/usage/record",
                    json={"model_name": "claude"},
                )

        assert resp.status_code == 422

    async def test_record_usage_auth_required(self, mock_deps):
        """POST /usage/record with API key configured requires Bearer token."""
        with patch("src.api.routes.usage.settings") as mock_settings:
            mock_settings.mcp_ingest_api_key = "test-secret-key"

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # No auth header
                resp = await client.post(
                    "/api/v1/usage/record",
                    json={"interface": "test"},
                )

        assert resp.status_code == 401

    async def test_record_usage_auth_valid_key(self, mock_deps):
        """POST /usage/record with correct Bearer token succeeds."""
        mock_db = mock_deps["db"]

        def capture_add(obj):
            obj.id = 99

        mock_db.add = capture_add

        with patch("src.api.routes.usage.settings") as mock_settings:
            mock_settings.mcp_ingest_api_key = "test-secret-key"

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/usage/record",
                    json={"interface": "deep_dive"},
                    headers={"Authorization": "Bearer test-secret-key"},
                )

        assert resp.status_code == 200

    async def test_record_usage_auth_invalid_key(self, mock_deps):
        """POST /usage/record with wrong Bearer token gets 401."""
        with patch("src.api.routes.usage.settings") as mock_settings:
            mock_settings.mcp_ingest_api_key = "real-key"

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/usage/record",
                    json={"interface": "test"},
                    headers={"Authorization": "Bearer wrong-key"},
                )

        assert resp.status_code == 401
