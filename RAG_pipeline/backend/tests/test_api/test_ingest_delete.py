from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from src.api.main import app


async def test_delete_project_document_success(monkeypatch):
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")

    doc = MagicMock()
    doc.id = 42
    result = MagicMock()
    result.scalar_one_or_none.return_value = doc

    mock_db = AsyncMock()
    mock_db.execute.return_value = result
    mock_qdrant = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    try:
        with patch("src.api.routes.ingest.delete_document_points", new_callable=AsyncMock) as mock_delete_points:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(
                    "/api/v1/ingest/by-project/proj/document/01_doc.md",
                    headers={"Authorization": "Bearer test-key"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "project_id": "proj", "filename": "01_doc.md"}
    mock_delete_points.assert_awaited_once_with(mock_qdrant, 42)
    mock_db.delete.assert_awaited_once_with(doc)
    mock_db.commit.assert_awaited_once()


async def test_delete_project_document_missing_is_noop(monkeypatch):
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")

    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.return_value = result
    mock_qdrant = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                "/api/v1/ingest/by-project/proj/document/01_doc.md",
                headers={"Authorization": "Bearer test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"deleted": False, "project_id": "proj", "filename": "01_doc.md"}
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_not_called()


async def test_delete_project_document_requires_api_key(monkeypatch):
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/ingest/by-project/proj/document/01_doc.md")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 401
