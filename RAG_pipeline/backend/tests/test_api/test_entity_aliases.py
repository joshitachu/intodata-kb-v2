"""Tests for internal-layer delete and entity alias replace endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from src.api.main import app


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_delete_internal_documents_scoped_to_internal_prefix(monkeypatch):
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")

    internal_doc = MagicMock()
    internal_doc.id = 7
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [internal_doc]
    alias_delete_result = MagicMock()
    alias_delete_result.rowcount = 3

    mock_db = AsyncMock()
    mock_db.execute.side_effect = [select_result, MagicMock(), alias_delete_result]
    mock_qdrant = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    try:
        with patch(
            "src.api.routes.ingest.delete_document_points", new_callable=AsyncMock
        ) as mock_delete_points:
            async with _client() as client:
                resp = await client.delete(
                    "/api/v1/ingest/by-project/proj/internal",
                    headers={"Authorization": "Bearer test-key"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {
        "deleted_documents": 1,
        "deleted_aliases": 3,
        "project_id": "proj",
    }
    mock_delete_points.assert_awaited_once_with(mock_qdrant, 7)
    # The document select must scope on the internal prefix — escaped, so "_"
    # in project ids or the prefix can never act as a LIKE wildcard.
    select_stmt = mock_db.execute.await_args_list[0].args[0]
    compiled = str(select_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "/_internal" in compiled, "filter must scope on the internal prefix"
    assert "ESCAPE" in compiled.upper(), (
        "the '_' in the prefix (and in project ids) must be escaped, "
        "not treated as a LIKE single-char wildcard"
    )
    mock_db.commit.assert_awaited_once()


async def test_delete_internal_documents_requires_api_key(monkeypatch):
    from src.api.dependencies import get_db, get_qdrant
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    try:
        async with _client() as client:
            resp = await client.delete("/api/v1/ingest/by-project/proj/internal")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 401


async def test_replace_entity_aliases(monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")

    delete_result = MagicMock()
    delete_result.rowcount = 2
    mock_db = AsyncMock()
    mock_db.execute.return_value = delete_result
    mock_db.add_all = MagicMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        async with _client() as client:
            resp = await client.post(
                "/api/v1/ingest/entity-aliases",
                headers={"Authorization": "Bearer test-key"},
                json={
                    "project_id": "proj",
                    "aliases": [
                        {
                            "alias": "SALESTABLE",
                            "alias_normalized": "salestable",
                            "canonical_name": "SALESTABLE",
                            "entity_type": "table",
                            "unit_name": "sjItem_OffRamp_DB",
                            "source_path": "Item/Out/sjItem_OffRamp_DB_0.1.item",
                            "source_artifact": "entity_index.csv",
                            "confidence": 0.95,
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"project_id": "proj", "deleted": 2, "inserted": 1}
    inserted = list(mock_db.add_all.call_args.args[0])
    assert len(inserted) == 1
    assert inserted[0].alias_normalized == "salestable"
    assert inserted[0].project_id == "proj"
    mock_db.commit.assert_awaited_once()


async def test_replace_entity_aliases_empty_list_clears_project(monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")

    delete_result = MagicMock()
    delete_result.rowcount = 5
    mock_db = AsyncMock()
    mock_db.execute.return_value = delete_result
    mock_db.add_all = MagicMock()

    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        async with _client() as client:
            resp = await client.post(
                "/api/v1/ingest/entity-aliases",
                headers={"Authorization": "Bearer test-key"},
                json={"project_id": "proj", "aliases": []},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"project_id": "proj", "deleted": 5, "inserted": 0}


async def test_replace_entity_aliases_requires_api_key(monkeypatch):
    from src.api.dependencies import get_db
    from src.config import settings

    monkeypatch.setattr(settings, "mcp_ingest_api_key", "test-key")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    try:
        async with _client() as client:
            resp = await client.post(
                "/api/v1/ingest/entity-aliases",
                json={"project_id": "proj", "aliases": []},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 401
