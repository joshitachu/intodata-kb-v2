import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User, Role, Document, QueryLog, IngestionJob


class TestUserModel:
    async def test_create_user(self, db_session: AsyncSession) -> None:
        user = User(username="testuser", email="test@example.com")
        db_session.add(user)
        await db_session.flush()

        result = await db_session.execute(select(User).where(User.username == "testuser"))
        fetched = result.scalar_one()
        assert fetched.username == "testuser"
        assert fetched.email == "test@example.com"
        assert fetched.id is not None

    async def test_username_unique(self, db_session: AsyncSession) -> None:
        user1 = User(username="unique", email="a@example.com")
        user2 = User(username="unique", email="b@example.com")
        db_session.add(user1)
        await db_session.flush()
        db_session.add(user2)
        with pytest.raises(Exception):  # IntegrityError wrapped by driver
            await db_session.flush()


class TestRoleModel:
    async def test_create_role(self, db_session: AsyncSession) -> None:
        role = Role(name="admin", description="Administrator group")
        db_session.add(role)
        await db_session.flush()

        result = await db_session.execute(select(Role).where(Role.name == "admin"))
        fetched = result.scalar_one()
        assert fetched.name == "admin"
        assert fetched.description == "Administrator group"


class TestUserRoleM2M:
    async def test_assign_roles_to_user(self, db_session: AsyncSession) -> None:
        role_a = Role(name="engineering", description="Engineering team")
        role_b = Role(name="sales", description="Sales team")
        user = User(username="multi", email="multi@example.com")
        user.roles = [role_a, role_b]

        db_session.add(user)
        await db_session.flush()

        result = await db_session.execute(
            select(User).where(User.username == "multi")
        )
        fetched = result.scalar_one()
        role_names = {r.name for r in fetched.roles}
        assert role_names == {"engineering", "sales"}

    async def test_role_has_users(self, db_session: AsyncSession) -> None:
        role = Role(name="shared", description="Shared role")
        user1 = User(username="u1", email="u1@example.com")
        user2 = User(username="u2", email="u2@example.com")
        user1.roles = [role]
        user2.roles = [role]

        db_session.add_all([user1, user2])
        await db_session.flush()

        result = await db_session.execute(select(Role).where(Role.name == "shared"))
        fetched = result.scalar_one()
        assert len(fetched.users) == 2


class TestDocumentModel:
    async def test_create_document(self, db_session: AsyncSession) -> None:
        user = User(username="uploader", email="up@example.com")
        db_session.add(user)
        await db_session.flush()

        doc = Document(
            filename="report.pdf",
            file_type="pdf",
            source="/uploads/report.pdf",
            author="John Doe",
            department="Engineering",
            chunk_count=10,
            uploaded_by=user.id,
        )
        db_session.add(doc)
        await db_session.flush()

        result = await db_session.execute(
            select(Document).where(Document.filename == "report.pdf")
        )
        fetched = result.scalar_one()
        assert fetched.file_type == "pdf"
        assert fetched.chunk_count == 10
        assert fetched.uploaded_by == user.id

    async def test_document_optional_fields(self, db_session: AsyncSession) -> None:
        doc = Document(filename="minimal.docx", file_type="docx")
        db_session.add(doc)
        await db_session.flush()

        assert doc.author is None
        assert doc.department is None
        assert doc.uploaded_by is None
        assert doc.chunk_count == 0


class TestQueryLogModel:
    async def test_create_query_log(self, db_session: AsyncSession) -> None:
        user = User(username="querier", email="q@example.com")
        db_session.add(user)
        await db_session.flush()

        log = QueryLog(
            user_id=user.id,
            query_text="What is our revenue?",
            response_text="Revenue was $1M in Q4.",
            sources={"citations": [{"file": "report.pdf", "page": 3}]},
            latency_ms=450,
        )
        db_session.add(log)
        await db_session.flush()

        result = await db_session.execute(select(QueryLog).where(QueryLog.id == log.id))
        fetched = result.scalar_one()
        assert fetched.query_text == "What is our revenue?"
        assert fetched.latency_ms == 450
        assert fetched.sources["citations"][0]["file"] == "report.pdf"


class TestIngestionJobModel:
    async def test_create_ingestion_job(self, db_session: AsyncSession) -> None:
        doc = Document(filename="data.pptx", file_type="pptx")
        db_session.add(doc)
        await db_session.flush()

        job = IngestionJob(document_id=doc.id, status="pending")
        db_session.add(job)
        await db_session.flush()

        result = await db_session.execute(
            select(IngestionJob).where(IngestionJob.document_id == doc.id)
        )
        fetched = result.scalar_one()
        assert fetched.status == "pending"
        assert fetched.chunks_created == 0
        assert fetched.error_message is None

    async def test_ingestion_job_status_flow(self, db_session: AsyncSession) -> None:
        doc = Document(filename="flow.pdf", file_type="pdf")
        db_session.add(doc)
        await db_session.flush()

        job = IngestionJob(document_id=doc.id, status="pending")
        db_session.add(job)
        await db_session.flush()

        job.status = "processing"
        await db_session.flush()
        assert job.status == "processing"

        job.status = "completed"
        job.chunks_created = 25
        await db_session.flush()
        assert job.status == "completed"
        assert job.chunks_created == 25

    async def test_ingestion_job_cascade_delete(self, db_session: AsyncSession) -> None:
        doc = Document(filename="delete_me.pdf", file_type="pdf")
        db_session.add(doc)
        await db_session.flush()

        job = IngestionJob(document_id=doc.id, status="completed", chunks_created=5)
        db_session.add(job)
        await db_session.flush()

        await db_session.delete(doc)
        await db_session.flush()

        result = await db_session.execute(
            select(IngestionJob).where(IngestionJob.document_id == doc.id)
        )
        assert result.scalar_one_or_none() is None
