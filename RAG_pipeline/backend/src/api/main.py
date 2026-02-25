import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.api.routes import health, query, documents, ingest, test, conversations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    logging.basicConfig(level=getattr(logging, settings.log_level))
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logger.info("Starting IntoData KB API")

    # Ensure Qdrant collection and payload indexes exist
    from src.api.dependencies import get_qdrant
    from src.retrieval.vector_store import ensure_collection
    qdrant = await get_qdrant()
    await ensure_collection(qdrant)
    logger.info("Qdrant collection and indexes verified")

    if settings.reranker_enabled:
        from src.retrieval.reranker import warmup as warmup_reranker
        warmup_reranker()

    yield
    logger.info("Shutting down IntoData KB API")
    # Cleanup: close Qdrant client if open
    from src.api.dependencies import _qdrant_client
    if _qdrant_client is not None:
        await _qdrant_client.close()


app = FastAPI(
    title="IntoData Knowledge Base",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(test.router)
app.include_router(query.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
