"""Seed the database with test data and ingest a PDF into Qdrant.

Run with:  cd backend && python -m scripts.seed_data
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import sys
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import (
    Conversation,
    ConversationMessage,
    Document,
    IngestionJob,
    QueryLog,
    Role,
    User,
    user_roles,
)
from src.db.session import async_session_factory
from src.ingestion.pipeline import ingest_document

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PDF_PATH = Path(__file__).resolve().parent.parent / "intodata_implementation_plan.md.pdf"


async def seed_test_data(db: AsyncSession) -> dict[str, User]:
    """Insert test rows into every table. Returns created users by username."""

    # Check if data already exists
    existing = (await db.execute(select(User))).scalars().first()
    if existing:
        logger.info("Test data already exists — skipping seed.")
        users_result = await db.execute(select(User))
        users = {u.username: u for u in users_result.scalars().all()}
        return users

    # --- Roles ---
    admin_role = Role(name="admin", description="Full access to all documents and settings")
    viewer_role = Role(name="viewer", description="Read-only access to documents")
    db.add_all([admin_role, viewer_role])
    await db.flush()
    logger.info("Created roles: admin, viewer")

    # --- Users ---
    john = User(username="johndoe", email="john@intodata.be")
    jane = User(username="janedoe", email="jane@intodata.be")
    db.add_all([john, jane])
    await db.flush()
    logger.info(f"Created users: johndoe (id={john.id}), janedoe (id={jane.id})")

    # --- user_roles ---
    await db.execute(user_roles.insert().values(user_id=john.id, role_id=admin_role.id))
    await db.execute(user_roles.insert().values(user_id=jane.id, role_id=viewer_role.id))
    logger.info("Assigned roles: johndoe→admin, janedoe→viewer")

    # --- Documents ---
    doc1 = Document(
        filename="company-handbook.pdf",
        file_type="pdf",
        source="/docs/handbook.pdf",
        author="John Doe",
        department="HR",
        chunk_count=12,
        uploaded_by=john.id,
    )
    doc2 = Document(
        filename="quarterly-report.docx",
        file_type="docx",
        source="/docs/q4-report.docx",
        author="Jane Doe",
        department="Finance",
        chunk_count=8,
        uploaded_by=jane.id,
    )
    db.add_all([doc1, doc2])
    await db.flush()
    logger.info(f"Created documents: {doc1.filename} (id={doc1.id}), {doc2.filename} (id={doc2.id})")

    # --- Ingestion Jobs ---
    now = datetime.datetime.now(datetime.timezone.utc)
    job1 = IngestionJob(
        document_id=doc1.id,
        status="completed",
        chunks_created=12,
        started_at=now - datetime.timedelta(minutes=5),
        completed_at=now - datetime.timedelta(minutes=4),
    )
    job2 = IngestionJob(
        document_id=doc2.id,
        status="failed",
        chunks_created=0,
        error_message="Unsupported encoding",
        started_at=now - datetime.timedelta(minutes=3),
        completed_at=now - datetime.timedelta(minutes=3),
    )
    db.add_all([job1, job2])
    await db.flush()
    logger.info("Created ingestion jobs: 1 completed, 1 failed")

    # --- Query Logs ---
    ql1 = QueryLog(
        query_text="What is the vacation policy?",
        response_text="Employees get 20 days of paid vacation per year, as outlined in the company handbook.",
        sources=[{"filename": "company-handbook.pdf", "page": 15}],
        latency_ms=1200,
        user_id=john.id,
    )
    ql2 = QueryLog(
        query_text="What were Q4 revenues?",
        response_text="Q4 revenues were approximately €2.3M, a 12% increase from the previous quarter.",
        sources=[{"filename": "quarterly-report.docx", "page": 3}],
        latency_ms=950,
        user_id=jane.id,
    )
    db.add_all([ql1, ql2])
    await db.flush()
    logger.info("Created query logs: 2 entries")

    # --- Conversation + Messages ---
    convo = Conversation(title="Vacation policy question", user_id=john.id)
    db.add(convo)
    await db.flush()

    msg1 = ConversationMessage(
        conversation_id=convo.id,
        role="user",
        content="What is the vacation policy?",
    )
    msg2 = ConversationMessage(
        conversation_id=convo.id,
        role="assistant",
        content="According to the company handbook, employees get 20 vacation days per year.",
        sources=[{"filename": "company-handbook.pdf", "page": 15}],
        latency_ms=1200,
    )
    db.add_all([msg1, msg2])
    await db.flush()
    logger.info(f"Created conversation (id={convo.id}) with 2 messages")

    return {"johndoe": john, "janedoe": jane}


async def ingest_pdf(db: AsyncSession, qdrant: AsyncQdrantClient, uploader_id: int) -> None:
    """Ingest the implementation plan PDF into Qdrant."""
    if not PDF_PATH.exists():
        logger.error(f"PDF not found at {PDF_PATH}")
        return

    logger.info(f"Reading PDF: {PDF_PATH.name} ({PDF_PATH.stat().st_size:,} bytes)")
    file_bytes = PDF_PATH.read_bytes()

    doc, job = await ingest_document(
        file_bytes=file_bytes,
        filename=PDF_PATH.name,
        allowed_groups=["admin"],
        uploaded_by=uploader_id,
        db=db,
        qdrant=qdrant,
    )
    logger.info(
        f"Ingestion complete: document_id={doc.id}, "
        f"chunks={job.chunks_created}, status={job.status}"
    )


async def main() -> None:
    logger.info("=== Starting seed script ===")

    qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

    async with async_session_factory() as db:
        try:
            # Part 1: Seed test data
            logger.info("--- Part 1: Seeding test data ---")
            users = await seed_test_data(db)
            await db.commit()
            logger.info("Test data committed to PostgreSQL.")

            # Part 2: Ingest PDF
            logger.info("--- Part 2: Ingesting PDF into Qdrant ---")
            john = users["johndoe"]
            await ingest_pdf(db, qdrant, uploader_id=john.id)
            await db.commit()
            logger.info("PDF ingestion committed.")

        except Exception:
            await db.rollback()
            logger.exception("Seed script failed — rolled back.")
            sys.exit(1)

    await qdrant.close()
    logger.info("=== Seed script finished successfully ===")


if __name__ == "__main__":
    asyncio.run(main())
