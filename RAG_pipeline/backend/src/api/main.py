import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.api.middleware.csrf import CSRFMiddleware
from src.api.middleware.ip_allowlist import IPAllowlistMiddleware
from src.api.middleware.metrics import PrometheusMiddleware
from src.api.middleware.request_id import RequestIDMiddleware
from src.config import settings
from src.logging_config import configure_logging
from src.api.routes import health, query, documents, ingest, conversations, benchmark, usage, projects_api, memory, ops_metrics as ops_metrics_routes
from src.api.routes import auth as auth_routes
from src.api.routes import admin as admin_routes
from src.api.routes import instance_settings as instance_settings_routes
from src.api.routes import usage_admin as usage_admin_routes
from src.api.routes import sso as sso_routes
from src.api.routes import onboarding as onboarding_routes
from src.api.routes import notifications as notifications_routes
from src.api.routes import telemetry as telemetry_routes
from src.api.routes import tenant as tenant_routes
from src.api.routes import feedback as feedback_routes
from src.api.routes import company_onboarding as company_onboarding_routes
from src.api.routes import runtime as runtime_routes
from src.api.routes import runtime_sources as runtime_source_routes
from src.api.routes import runtime_source_types as runtime_source_type_routes
from src.api.routes import runtime_workspace_map as runtime_workspace_map_routes
from src.api.routes import runtime_audit as runtime_audit_routes
from src.api.routes import harness as harness_routes
from src.api.routes import harness_connectors as harness_connector_routes
from src.api.routes import harness_packs as harness_pack_routes
from src.api.routes import agent_traces as agent_trace_routes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    configure_logging(settings.log_level)
    logger.info("Starting IntoData KB API")
    runtime_scheduler = None
    documentation_reconciliation_scheduler = None

    # Ensure Qdrant collection and payload indexes exist
    from src.api.dependencies import get_qdrant
    from src.retrieval.vector_store import ensure_collection
    qdrant = await get_qdrant()
    await ensure_collection(qdrant)
    logger.info("Qdrant collection and indexes verified")

    if settings.reranker_enabled:
        from src.retrieval.reranker import warmup as warmup_reranker
        warmup_reranker()

    # Seed a convenience admin for development only. In production we intentionally
    # leave the users table empty so the onboarding wizard's POST /auth/bootstrap-admin
    # is the single code path that creates the first real admin.
    from src.db.session import get_db
    from src.db.models import User, Role, Permission
    from src.auth.security import hash_password
    from sqlalchemy import select, text
    async for db in get_db():
        if settings.app_env != "production":
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
                    email=settings.admin_initial_email,
                    password_hash=hash_password(settings.admin_default_password),
                    is_superadmin=True,
                )
                user.roles.append(role)
                db.add(user)
                await db.flush()
                await db.execute(text("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users))"))
                await db.execute(text("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles))"))
                await db.commit()
                logger.info("Seeded default admin user (id=1) with password [dev mode]")
            elif admin.password_hash is None:
                admin.password_hash = hash_password(settings.admin_default_password)
                admin.is_superadmin = True
                await db.commit()
                logger.info("Updated existing admin user with password hash [dev mode]")
        else:
            # Production: make sure the admin role row exists so bootstrap-admin can attach
            # to it without needing a separate bootstrap-role step. Roles/permissions
            # themselves are seeded by Alembic migrations (006, 012+).
            role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one_or_none()
            if role is None:
                role = Role(id=1, name="admin", description="Administrator")
                db.add(role)
                await db.flush()
                await db.execute(text("SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles))"))
                await db.commit()
                logger.info("Seeded admin role [production mode — users table left empty for bootstrap-admin]")

        # Verify RBAC permissions exist (idempotent check only)
        existing_perms = (await db.execute(select(Permission.codename))).scalars().all()
        if not existing_perms:
            logger.info("No permissions table data yet, skipping RBAC seed (run migration 006)")
        # Note: admin role permissions are managed by migrations (012+).

    if settings.runtime_sync_scheduler_enabled:
        from src.db.session import async_session_factory
        from src.runtime.sync import RuntimeSyncScheduler, parse_project_ids

        project_ids = parse_project_ids(settings.runtime_sync_project_ids)
        semantic_indexer = None
        if settings.runtime_semantic_enabled:
            from src.runtime.semantic.service import RuntimeSemanticService

            semantic_indexer = RuntimeSemanticService(qdrant)
        if settings.runtime_sync_source_key == "talend_tmc" and project_ids:
            from src.runtime.providers.talend_tmc import provider as talend_tmc_provider

            runtime_scheduler = RuntimeSyncScheduler(
                session_factory=async_session_factory,
                adapter_factory=lambda: talend_tmc_provider,
                source_key="talend_tmc",
                project_ids=project_ids,
                interval_seconds=settings.runtime_sync_interval_seconds,
                semantic_indexer=semantic_indexer,
            )
            runtime_scheduler.start_background()
            logger.info("Runtime sync scheduler started for source=talend_tmc")
        elif settings.runtime_sync_source_key == "mock" and project_ids:
            from src.runtime.providers.mock import provider as mock_runtime_provider

            runtime_scheduler = RuntimeSyncScheduler(
                session_factory=async_session_factory,
                adapter_factory=lambda: mock_runtime_provider,
                source_key="mock",
                project_ids=project_ids,
                interval_seconds=settings.runtime_sync_interval_seconds,
                semantic_indexer=semantic_indexer,
            )
            runtime_scheduler.start_background()
            logger.info("Runtime sync scheduler started for source=mock")
        else:
            logger.info("Runtime sync scheduler enabled but no supported source/projects configured")

    service_token = settings.mcp_docs_reconciliation_service_token
    if service_token is not None and service_token.get_secret_value().strip():
        from src.db.session import async_session_factory
        from src.harness.reconciliation import DocumentationWorkflowReconciliationScheduler

        documentation_reconciliation_scheduler = DocumentationWorkflowReconciliationScheduler(
            session_factory=async_session_factory,
            service_access_token=service_token.get_secret_value(),
            interval_seconds=settings.mcp_docs_reconciliation_interval_seconds,
            batch_size=settings.mcp_docs_reconciliation_batch_size,
        )
        documentation_reconciliation_scheduler.start_background()
        logger.info("Documentation workflow reconciliation scheduler started")
    else:
        logger.info("Documentation workflow reconciliation scheduler disabled; no service credential configured")

    yield
    logger.info("Shutting down IntoData KB API")
    if runtime_scheduler is not None:
        await runtime_scheduler.stop()
    if documentation_reconciliation_scheduler is not None:
        await documentation_reconciliation_scheduler.stop()
    # Cleanup: close Qdrant client if open
    from src.api.dependencies import _qdrant_client
    if _qdrant_client is not None:
        await _qdrant_client.close()
    # Cleanup: close singleton LLM clients
    from src.generation.llm_client import cleanup_llm_clients
    await cleanup_llm_clients()


app = FastAPI(
    title="IntoData Knowledge Base",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting
from src.api.routes.auth import limiter  # noqa: E402
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# IP allowlist — no-op when ALLOWED_IPS is empty. Added BEFORE ProxyHeadersMiddleware
# in registration order means it runs AFTER in dispatch order (Starlette stacks LIFO),
# so by the time IP allowlist checks request.client.host the proxy headers have already
# rewritten it to the real client IP.
app.add_middleware(IPAllowlistMiddleware, allowed_ips=settings.allowed_ips)

# Rewrite request.client.host from X-Forwarded-For when the incoming connection comes
# from a trusted proxy. Without this, slowapi rate limits and the IP allowlist would
# see the reverse proxy's IP for every request.
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts=[p.strip() for p in settings.trusted_proxies.split(",") if p.strip()]
    or "127.0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Cookie"],
)

# Registered last → outermost wrapper → runs first on every request.
# Generates a UUID4 request ID (or propagates X-Request-ID from caller) and
# stores it in a context var so all log calls within the request carry it.
app.add_middleware(RequestIDMiddleware)

# CSRF double-submit cookie validation. Registered after RequestIDMiddleware so
# it runs before the request reaches route handlers. Disabled via CSRF_ENABLED=false
# in test environments (avoids updating every existing test with a token header).
if settings.csrf_enabled:
    app.add_middleware(CSRFMiddleware)

# PrometheusMiddleware wraps everything — registered after RequestIDMiddleware so
# it's outermost and measures end-to-end latency including request-ID overhead.
# No auth required on /metrics; rely on network-level access control (nginx allowlist).
if settings.metrics_enabled:
    app.add_middleware(PrometheusMiddleware)

# Prometheus scrape endpoint — no auth, gate at network level
if settings.metrics_enabled:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

# Routes
app.include_router(health.router)
app.include_router(auth_routes.router, prefix="/api/v1")
app.include_router(sso_routes.router, prefix="/api/v1")
app.include_router(admin_routes.router, prefix="/api/v1")
app.include_router(instance_settings_routes.router, prefix="/api/v1")
app.include_router(usage_admin_routes.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(benchmark.router, prefix="/api/v1")
app.include_router(usage.router, prefix="/api/v1")
app.include_router(projects_api.router, prefix="/api/v1")
app.include_router(memory.router, prefix="/api/v1")
app.include_router(onboarding_routes.router, prefix="/api/v1")
app.include_router(company_onboarding_routes.router, prefix="/api/v1")
app.include_router(notifications_routes.router, prefix="/api/v1")
app.include_router(telemetry_routes.router, prefix="/api/v1")
app.include_router(tenant_routes.router, prefix="/api/v1")
app.include_router(feedback_routes.router, prefix="/api/v1")
app.include_router(ops_metrics_routes.router, prefix="/api/v1")
app.include_router(runtime_routes.router, prefix="/api/v1")
app.include_router(runtime_source_routes.router, prefix="/api/v1")
app.include_router(runtime_source_type_routes.router, prefix="/api/v1")
app.include_router(runtime_workspace_map_routes.router, prefix="/api/v1")
app.include_router(runtime_audit_routes.router, prefix="/api/v1")
app.include_router(harness_routes.router, prefix="/api/v1")
app.include_router(harness_connector_routes.router, prefix="/api/v1")
app.include_router(harness_pack_routes.router, prefix="/api/v1")
app.include_router(agent_trace_routes.router, prefix="/api/v1")
