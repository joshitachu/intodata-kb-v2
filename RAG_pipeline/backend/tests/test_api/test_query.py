from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role
from src.schemas import DocumentSource, QueryResponse, Source


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _mock_user():
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = 1
    user.roles = [role]
    return user


@pytest.fixture
def mock_deps():
    """Override all FastAPI dependencies for isolated testing."""
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


async def test_query_endpoint_success(mock_deps):
    response_data = QueryResponse(
        answer="The answer is 42.",
        sources=[
            DocumentSource(
                document_id=1,
                filename="doc.pdf",
                score=0.92,
                text="Some text",
                reference_number=1,
            )
        ],
        latency_ms=150,
    )

    with (
        patch("src.api.routes.query.run_rag_query", new_callable=AsyncMock) as mock_rag,
        patch("src.api.routes.query.active_knowledge_revisions", new_callable=AsyncMock, return_value={}),
    ):
        mock_rag.return_value = response_data

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/query",
                json={"query": "What is 42?"},
                headers=CSRF_HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "The answer is 42."
    assert len(data["sources"]) == 1


async def test_query_endpoint_validation_error(mock_deps):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/query",
            json={"query": "ab"},  # too short (min_length=3)
            headers=CSRF_HEADERS,
        )
    assert resp.status_code == 422


async def test_query_debug_endpoint(mock_deps):
    mock_point = MagicMock()
    mock_point.score = 0.45
    mock_point.payload = {
        "document_id": 1,
        "filename": "doc.pdf",
        "chunk_index": 0,
        "text": "Some relevant text",
    }
    mock_result = MagicMock()
    mock_result.points = [mock_point]
    mock_deps["qdrant"].query_points.return_value = mock_result

    with (
        patch("src.api.routes.query.embed_text", new_callable=AsyncMock) as mock_embed,
        patch("src.api.routes.query.active_knowledge_revisions", new_callable=AsyncMock, return_value={}),
    ):
        mock_embed.return_value = [0.1] * 768

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/query/debug",
                json={"query": "What is onboarding?"},
                headers=CSRF_HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "What is onboarding?"
    assert data["total_results"] == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["score"] == 0.45
    assert data["embedding_ms"] >= 0
