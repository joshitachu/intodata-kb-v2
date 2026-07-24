"""FastAPI application for the MCP server REST API."""

from __future__ import annotations

import datetime
import logging
import os
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from sqlalchemy import inspect, update
from sqlalchemy.future import select
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from mcp_server.api.middleware import IPAllowlistMiddleware
from mcp_server.config import check_provider_credentials, config
from mcp_server.db.models import Base, GenerateRun
from mcp_server.db.session import async_session_factory, engine
from mcp_server.services.atomic_swap import (
    gc_abandoned_staging,
    recover_stale_backups,
)
from mcp_server.services.generation_run_state import lock_path, read_json_file
from mcp_server.services.log_manager import LogManager
from mcp_server.services.git_poller import git_polling_loop
from mcp_server.services.ops_telemetry import telemetry_enabled, telemetry_loop
from mcp_server.services.project_paths import repair_managed_project_repo_paths
from mcp_server.services.pack_governance import pack_governance

logger = logging.getLogger(__name__)

# Shared log manager instance
log_manager = LogManager()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _reconcile_orphaned_runs() -> None:
    """Mark GenerateRun rows left in status='running' at crash time as failed."""
    output_root = config.project_root / "output"
    now = datetime.datetime.now(datetime.timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(
            select(GenerateRun).where(GenerateRun.status == "running")
        )
        runs = result.scalars().all()
        if not runs:
            return
        orphaned_ids: list[int] = []
        for run in runs:
            staging_dir = output_root / run.project_id / "docs_staging"
            lock_data = read_json_file(lock_path(staging_dir))
            if lock_data is None:
                orphaned_ids.append(run.id)
                continue
            pid = lock_data.get("pid")
            if pid and not _pid_alive(int(pid)):
                orphaned_ids.append(run.id)
                continue
            age = time.time() - float(lock_data.get("updated_at") or 0)
            if age > config.doc_generation_lock_stale_seconds:
                orphaned_ids.append(run.id)
        if orphaned_ids:
            await session.execute(
                update(GenerateRun)
                .where(GenerateRun.id.in_(orphaned_ids))
                .values(
                    status="failed",
                    error_message="orphaned at restart",
                    completed_at=now,
                )
            )
            await session.commit()
            logger.warning(
                "[startup] marked %d orphaned generate_run(s) as failed: ids=%s",
                len(orphaned_ids), orphaned_ids,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    logging.basicConfig(level=getattr(logging, config.log_level))
    logger.info("Starting intodoc MCP Server API")
    # Auto-create tables on first run, skip if they already exist
    async with engine.begin() as conn:
        existing = set(
            await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        )
        tables_to_create = [
            t for t in Base.metadata.sorted_tables if t.name not in existing
        ]
        if tables_to_create:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=tables_to_create
                )
            )
            logger.info("Created tables: %s", [t.name for t in tables_to_create])
        else:
            logger.info("All tables already exist, skipping create")
    try:
        repaired_paths = await repair_managed_project_repo_paths(async_session_factory)
        if repaired_paths:
            logger.warning("Repaired %d stale project repo path(s)", repaired_paths)
    except Exception:
        logger.exception("Startup project path repair failed")
    try:
        async with async_session_factory() as session:
            migrated_packs = await pack_governance.backfill_explicit_legacy_packs(session)
        if migrated_packs:
            logger.info("Certified %d explicitly configured legacy Pack(s)", migrated_packs)
    except Exception:
        logger.exception("Startup Pack governance migration failed")
    app.state.log_manager = log_manager
    app.state.git_poller_task = asyncio.create_task(git_polling_loop(log_manager))
    app.state.ops_telemetry_task = (
        asyncio.create_task(telemetry_loop()) if telemetry_enabled() else None
    )

    # Reconcile docs dirs from any previous crash: promote `docs_prev/` to
    # `docs/` if docs is missing, or remove stale backups if both exist.
    # Also GC abandoned `docs_staging/` dirs older than the configured window.
    output_root = config.project_root / "output"
    try:
        recovered = recover_stale_backups(output_root)
        if recovered:
            logger.info("Recovered %d stale docs backup(s) on startup", recovered)
        gc_count = gc_abandoned_staging(output_root, config.staging_gc_days)
        if gc_count:
            logger.info(
                "GC removed %d abandoned docs_staging dir(s) (> %d days)",
                gc_count, config.staging_gc_days,
            )
    except Exception:
        # Never let startup maintenance prevent the server coming up.
        logger.exception("Startup docs maintenance failed")

    try:
        await _reconcile_orphaned_runs()
    except Exception:
        logger.exception("Startup orphaned-run reconciliation failed")

    for warning in check_provider_credentials():
        if config.app_env == "production":
            logger.error("[startup] %s", warning)
        else:
            logger.warning("[startup] %s", warning)

    try:
        yield
    finally:
        telemetry_task = getattr(app.state, "ops_telemetry_task", None)
        if telemetry_task:
            telemetry_task.cancel()
            try:
                await telemetry_task
            except asyncio.CancelledError:
                pass
        poller_task = getattr(app.state, "git_poller_task", None)
        if poller_task:
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass
    logger.info("Shutting down intodoc MCP Server API")


# --- JWT auth dependency ---

async def require_auth(
    request: Request,
    access_token: str | None = Cookie(default=None),
) -> dict:
    """Validate JWT from access_token cookie. Returns decoded payload.

    Also stores the payload on request.state.auth_payload for downstream use.
    """
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            access_token,
            config.jwt_secret_key,
            algorithms=[config.jwt_algorithm],
        )
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        request.state.auth_payload = payload
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


app = FastAPI(
    title="intodoc MCP Server",
    version="0.1.0",
    lifespan=lifespan,
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if os.environ.get("MCP_COOKIE_SECURE", "").lower() == "true":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# See RAG backend main.py for the rationale — IP allowlist runs after ProxyHeaders
# in dispatch order, so it sees the real client IP (not the proxy's IP).
app.add_middleware(IPAllowlistMiddleware, allowed_ips=config.allowed_ips)
app.add_middleware(
    ProxyHeadersMiddleware,
    trusted_hosts=[p.strip() for p in config.trusted_proxies.split(",") if p.strip()]
    or "127.0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in config.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Cookie"],
)

# Import and register routes
from mcp_server.api.routes import discovery, estimate as estimate_routes, generate_runs, health, instance_settings, logs, ops_control, plugins, projects, webhook  # noqa: E402

app.include_router(health.router)  # /health stays open
app.include_router(ops_control.router)  # Mission Control triggers — own Bearer auth
app.include_router(
    projects.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    discovery.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    instance_settings.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    generate_runs.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)
app.include_router(
    plugins.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)
app.include_router(webhook.router)  # GitHub HMAC auth — no JWT cookie
app.include_router(
    logs.router,
    dependencies=[Depends(require_auth)],
)
app.include_router(
    estimate_routes.router,
    prefix="/mcp",
    dependencies=[Depends(require_auth)],
)

# --- Static file serving for production dashboard ---
_dist_dir = os.environ.get("DASHBOARD_DIST_DIR")
if _dist_dir:
    _dist_path = Path(_dist_dir)
    if _dist_path.is_dir():
        # Serve JS/CSS bundles at /assets
        _assets = _dist_path / "assets"
        if _assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

        # SPA catch-all: serve index.html for any non-API path
        @app.get("/{full_path:path}")
        async def _serve_spa(full_path: str):
            index = _dist_path / "index.html"
            return FileResponse(str(index))

        logger.info("Serving dashboard from %s", _dist_path)
