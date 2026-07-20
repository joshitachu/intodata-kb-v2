import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base

# Import all models so Base.metadata knows about them
import src.db.models  # noqa: F401

collect_ignore = ["stress_test.py"]

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


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

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncSession:
    """Yield an async session that rolls back after each test."""
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture(autouse=True)
def isolated_harness_runtime(monkeypatch):
    """Keep workflow tests on checkpointers isolated from the configured DB."""
    from langgraph.checkpoint.memory import InMemorySaver

    from src.harness import runtime
    from src.harness import workflow_resume
    from src.harness.workflows.onboarding import ProjectOnboardingWorkflow

    onboarding_checkpointer = InMemorySaver()

    monkeypatch.setattr(
        runtime,
        "_DEFAULT_RUNTIME_PROVIDER",
        runtime.LangGraphRuntimeProvider(),
    )
    monkeypatch.setattr(
        workflow_resume,
        "default_project_onboarding_workflow",
        lambda: ProjectOnboardingWorkflow(checkpointer=onboarding_checkpointer),
    )
