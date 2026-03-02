"""FastAPI application for the MCP server REST API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from mcp_server.config import config
from mcp_server.db.models import Base
from mcp_server.db.session import engine
from mcp_server.services.log_manager import LogManager

logger = logging.getLogger(__name__)

# Shared log manager instance
log_manager = LogManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    logging.basicConfig(level=getattr(logging, config.log_level))
    logger.info("Starting intodoc MCP Server API on :8001")
    # Auto-create tables on first run, skip if they already exist
    async with engine.begin() as conn:
        existing = await conn.run_sync(
            lambda sync_conn: sync_conn.dialect.get_table_names(sync_conn)
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
    app.state.log_manager = log_manager
    yield
    logger.info("Shutting down intodoc MCP Server API")


app = FastAPI(
    title="intodoc MCP Server",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternate dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routes
from mcp_server.api.routes import health, logs, projects, webhook  # noqa: E402

app.include_router(health.router)
app.include_router(projects.router, prefix="/mcp")
app.include_router(webhook.router)
app.include_router(logs.router)
