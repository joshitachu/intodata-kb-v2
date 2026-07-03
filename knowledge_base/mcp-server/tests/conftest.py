"""Shared test fixtures for MCP server tests."""

import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mcp_server.db.models import Base

# Import all models so Base.metadata is complete
from mcp_server.db.models import GenerateRun, InstanceSetting, Project, ScanRun  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Stub SQL for RAG pipeline tables that MCP server queries via raw SQL
_RBAC_STUB_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    is_superadmin BOOLEAN DEFAULT 0
);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY,
    codename TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER,
    permission_id INTEGER
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
CREATE TABLE IF NOT EXISTS project_assignments (
    user_id INTEGER,
    project_id TEXT,
    assigned_at DATETIME,
    created_at DATETIME,
    updated_at DATETIME,
    UNIQUE(user_id, project_id)
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at DATETIME
);
INSERT INTO users (id, username, is_superadmin) VALUES (1, 'testuser', 1);
"""


@pytest.fixture
async def engine():
    """Create an in-memory SQLite async engine for testing."""
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Enable SQLite foreign key enforcement (required for CASCADE)
    @event.listens_for(eng.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_conn.create_function("NOW", 0, lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Create stub RBAC tables that MCP endpoints query via raw SQL
        for stmt in _RBAC_STUB_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    """Return a session factory bound to the test engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory) -> AsyncSession:
    """Yield an async session that rolls back after each test."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def mock_auth():
    """Return a mock auth payload."""
    return {"sub": "1", "type": "access", "username": "testuser"}


@pytest.fixture
async def client(session_factory, monkeypatch, tmp_path):
    """Create an httpx AsyncClient with the FastAPI app, using SQLite test DB.

    Monkeypatches:
    - async_session_factory in all modules that import it
    - require_auth to bypass JWT
    - config paths to use tmp_path
    """
    # Monkeypatch config paths before importing the app
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config, "project_root", tmp_path / "project_root")
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc_output")

    # Create required directories
    (tmp_path / "data" / "logs").mkdir(parents=True)
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "data" / "repos").mkdir(parents=True)
    (tmp_path / "project_root" / "output").mkdir(parents=True)

    # Monkeypatch session factory in all modules
    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.api.routes.generate_runs as generate_runs_mod
    import mcp_server.api.routes.plugins as plugins_mod
    import mcp_server.api.routes.webhook as webhook_mod
    import mcp_server.api.routes.instance_settings as instance_settings_mod
    import mcp_server.api.permissions as permissions_mod
    import mcp_server.db.session as session_mod
    import mcp_server.services.git_poller as git_poller_mod
    import mcp_server.services.git_reingestion as git_reingestion_mod
    import mcp_server.services.git_diff_patcher as git_diff_patcher_mod
    import mcp_server.services.instance_settings_service as instance_settings_service_mod

    monkeypatch.setattr(session_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(generate_runs_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(plugins_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(webhook_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(instance_settings_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(permissions_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_poller_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_reingestion_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(git_diff_patcher_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(instance_settings_service_mod, "async_session_factory", session_factory)

    # Import app after monkeypatching config
    from fastapi import Request
    from mcp_server.api.app import app, require_auth

    # Override auth dependency to bypass JWT
    # NOTE: must use explicit Request annotation (no `from __future__ import annotations`)
    # so FastAPI can resolve it at runtime.
    async def _mock_auth(request: Request) -> dict:
        request.state.auth_payload = {"sub": "1", "type": "access", "username": "testuser"}
        return request.state.auth_payload

    app.dependency_overrides[require_auth] = _mock_auth

    # Set up log_manager on app state
    from mcp_server.services.log_manager import LogManager

    log_mgr = LogManager()
    monkeypatch.setattr(log_mgr, "_ensure_log_file", lambda pid: tmp_path / "data" / "logs" / f"{pid}_test.log")
    app.state.log_manager = log_mgr

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def log_manager(tmp_path, monkeypatch):
    """Return a LogManager with logs_dir pointing to tmp_path."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    (tmp_path / "logs").mkdir(exist_ok=True)

    from mcp_server.services.log_manager import LogManager

    return LogManager()
