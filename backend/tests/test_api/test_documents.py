from unittest.mock import AsyncMock, MagicMock, patch

import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role, Document


def _mock_user():
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = 1
    user.roles = [role]
    return user


def _mock_document(id: int = 1):
    doc = MagicMock(spec=Document)
    doc.id = id
    doc.filename = "test.pdf"
    doc.file_type = "pdf"
    doc.source = None
    doc.author = "Alice"
    doc.department = "Engineering"
    doc.chunk_count = 5
    doc.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    return doc


@pytest.fixture
def mock_deps():
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


async def test_list_documents(mock_deps):
    mock_db = mock_deps["db"]

    # Mock the count query
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1

    # Mock the select query
    doc = _mock_document()
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [doc]

    mock_db.execute = AsyncMock(side_effect=[count_result, select_result])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/documents")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["documents"]) == 1
    assert data["documents"][0]["filename"] == "test.pdf"


async def test_delete_document_success(mock_deps):
    mock_db = mock_deps["db"]

    doc = _mock_document(id=1)
    result = MagicMock()
    result.scalar_one_or_none.return_value = doc
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.delete = AsyncMock()

    with patch("src.api.routes.documents.delete_document_points", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/documents/1")

    assert resp.status_code == 204


async def test_delete_document_not_found(mock_deps):
    mock_db = mock_deps["db"]

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/v1/documents/999")

    assert resp.status_code == 404
