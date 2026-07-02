from unittest.mock import AsyncMock, MagicMock, patch

import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role, Document


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _mock_user(*, is_superadmin: bool = False, user_id: int = 1):
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = user_id
    user.is_superadmin = is_superadmin
    user.roles = [role]
    return user


def _mock_document(id: int = 1, *, uploaded_by: int | None = 1):
    doc = MagicMock(spec=Document)
    doc.id = id
    doc.filename = "test.pdf"
    doc.file_type = "pdf"
    doc.document_role = "guidance"
    doc.source = None
    doc.author = "Alice"
    doc.department = "Engineering"
    doc.project_id = "test-project"
    doc.chunk_count = 5
    doc.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    doc.uploaded_by = uploaded_by
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
    mock_deps["user"].is_superadmin = True

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
    assert data["documents"][0]["document_role"] == "guidance"


async def test_list_documents_scoped_to_caller_for_non_admin(mock_deps):
    """Non-admin user sees only their own uploads; the SQL filter must be applied."""
    from sqlalchemy import select as sa_select

    mock_db = mock_deps["db"]
    user = mock_deps["user"]
    user.is_superadmin = False
    user.id = 42

    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(side_effect=[count_result, select_result])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/documents")

    assert resp.status_code == 200
    # Two queries: count + select. Both must include a where-clause referencing the user.
    assert mock_db.execute.await_count == 2
    for call in mock_db.execute.await_args_list:
        stmt = call.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "uploaded_by" in compiled, (
            f"Expected uploaded_by filter on non-admin list query, got: {compiled}"
        )


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
            resp = await client.delete("/api/v1/documents/1", headers=CSRF_HEADERS)

    assert resp.status_code == 204


async def test_delete_document_not_found(mock_deps):
    mock_db = mock_deps["db"]

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/v1/documents/999", headers=CSRF_HEADERS)

    assert resp.status_code == 404


async def test_delete_document_forbidden_other_user_upload(mock_deps):
    """Non-admin user cannot delete a document uploaded by a different user."""
    mock_db = mock_deps["db"]
    user = mock_deps["user"]

    user.is_superadmin = False
    user.id = 1

    doc = _mock_document(id=1, uploaded_by=99)  # different uploader

    doc_result = MagicMock()
    doc_result.scalar_one_or_none.return_value = doc

    mock_db.execute = AsyncMock(return_value=doc_result)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/api/v1/documents/1", headers=CSRF_HEADERS)

    assert resp.status_code == 403


async def test_delete_document_allowed_for_owner(mock_deps):
    """Non-admin user can delete their own upload."""
    mock_db = mock_deps["db"]
    user = mock_deps["user"]

    user.is_superadmin = False
    user.id = 1

    doc = _mock_document(id=1, uploaded_by=1)

    doc_result = MagicMock()
    doc_result.scalar_one_or_none.return_value = doc
    mock_db.execute = AsyncMock(return_value=doc_result)

    with patch("src.api.routes.documents.delete_document_points", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/documents/1", headers=CSRF_HEADERS)

    assert resp.status_code == 204


async def test_delete_document_allowed_for_superadmin(mock_deps):
    """Superadmin can delete any document, regardless of uploader."""
    mock_db = mock_deps["db"]
    user = mock_deps["user"]
    user.is_superadmin = True
    user.id = 1

    doc = _mock_document(id=1, uploaded_by=999)

    doc_result = MagicMock()
    doc_result.scalar_one_or_none.return_value = doc
    mock_db.execute = AsyncMock(return_value=doc_result)

    with patch("src.api.routes.documents.delete_document_points", new_callable=AsyncMock):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/documents/1", headers=CSRF_HEADERS)

    assert resp.status_code == 204


# --- Soft delete tests (real SQLite DB via db_session) ---

async def test_soft_deleted_document_not_in_list(db_session):
    """A document with deleted_at set must not appear in GET /documents."""
    from datetime import datetime, timezone

    from src.api.dependencies import get_current_user, get_db
    from src.auth.security import hash_password
    from src.db.models import User

    user = User(
        username="doc-softdel-user",
        email="doc-softdel@example.com",
        password_hash=hash_password("pw"),
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    live_doc = Document(filename="live.pdf", file_type="pdf", chunk_count=1)
    dead_doc = Document(
        filename="deleted.pdf",
        file_type="pdf",
        chunk_count=1,
        deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([live_doc, dead_doc])
    await db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/documents")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    documents = resp.json()["documents"]
    filenames = [d["filename"] for d in documents]
    assert "live.pdf" in filenames
    assert "deleted.pdf" not in filenames
    live_response = next(d for d in documents if d["filename"] == "live.pdf")
    assert live_response["document_role"] == "source"


async def test_soft_deleted_document_returns_404_on_delete(db_session):
    """DELETE on an already-soft-deleted document must return 404."""
    from datetime import datetime, timezone

    from src.api.dependencies import get_current_user, get_db, get_qdrant
    from src.auth.security import hash_password
    from src.db.models import User

    user = User(
        username="doc-softdel2-user",
        email="doc-softdel2@example.com",
        password_hash=hash_password("pw"),
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    doc = Document(
        filename="already-deleted.pdf",
        file_type="pdf",
        chunk_count=1,
        deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(doc)
    await db_session.commit()

    mock_qdrant = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/v1/documents/{doc.id}", headers=CSRF_HEADERS)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
