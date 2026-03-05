import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.config import settings
from src.api.routes import health, query, documents, ingest, test, conversations
from src.api.routes import auth as auth_routes
from src.api.routes import admin as admin_routes

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

    # Ensure default admin user exists (seed data)
    from src.db.session import get_db
    from src.db.models import User, Role
    from src.auth.security import hash_password
    from sqlalchemy import select, text
    async for db in get_db():
        admin = (await db.execute(select(User).where(User.id == 1))).scalar_one_or_none()
        if admin is None:
            role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one_or_none()
            if role is None:
                role = Role(id=1, name="admin", description="Administrator")
                db.add(role)
                await db.flush()
            user = User(
                id=1,
                username="admin",
                email="admin@intodata.local",
                password_hash=hash_password(settings.admin_default_password),
                is_superadmin=True,
            )
            user.roles.append(role)
            db.add(user)
            await db.flush()
            # Reset sequences after explicit-ID inserts to avoid duplicate key errors
            await db.execute(text("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users))"))
            await db.execute(text("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles))"))
            await db.commit()
            logger.info("Seeded default admin user (id=1) with password")
        elif admin.password_hash is None:
            # Existing admin without password — set it
            admin.password_hash = hash_password(settings.admin_default_password)
            admin.is_superadmin = True
            await db.commit()
            logger.info("Updated existing admin user with password hash")

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

# Rate limiting
from src.api.routes.auth import limiter  # noqa: E402
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(test.router)
app.include_router(auth_routes.router, prefix="/api/v1")
app.include_router(admin_routes.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
