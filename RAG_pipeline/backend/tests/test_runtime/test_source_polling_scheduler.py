"""Tests for the source-driven runtime polling scheduler.

Unlike the legacy adapter scheduler, this one discovers registered sources from
the store each tick and syncs each enabled source on its own interval using that
source's stored credentials (the same path as the manual "Sync now" action).
"""

import datetime
from datetime import UTC

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.db.base import Base
from src.db.models import runtime_data  # noqa: F401
from src.runtime.models import RuntimeSourceUpdate, RuntimeSyncStatus
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSourcePollingScheduler

_T0 = datetime.datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture()
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield lambda: maker()
    await engine.dispose()


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    monkeypatch.setattr(settings, "source_credentials_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


async def _create_mock_source(session_factory, *, project_id: str, interval: int = 60) -> int:
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        source = await repo.create_source_config(
            project_id=project_id,
            source_type="mock",
            label=f"Mock {project_id}",
            sync_interval_seconds=interval,
        )
        await repo.write_credentials(
            source_id=source.id,
            plaintext_payload={"api_token": "secret"},
            credential_type="mock",
            rotated_by="1",
        )
        await session.commit()
        return source.id


async def test_run_once_syncs_enabled_sources_via_stored_credentials(session_factory):
    await _create_mock_source(session_factory, project_id="project-a")

    scheduler = RuntimeSourcePollingScheduler(
        session_factory=session_factory,
        now_factory=lambda: _T0,
    )
    results = await scheduler.run_once()

    assert len(results) == 1
    assert results[0].status == RuntimeSyncStatus.SUCCESS
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        sources = await repo.list_source_configs(None)
        assert sources[0].health is not None
        assert sources[0].health.last_attempt_at is not None


async def test_source_not_re_synced_before_its_interval_elapses(session_factory):
    await _create_mock_source(session_factory, project_id="project-a", interval=60)

    # First tick records an attempt; second tick at +30s is not yet due.
    first = RuntimeSourcePollingScheduler(session_factory=session_factory, now_factory=lambda: _T0)
    assert len(await first.run_once()) == 1

    not_due = RuntimeSourcePollingScheduler(
        session_factory=session_factory,
        now_factory=lambda: _T0 + datetime.timedelta(seconds=30),
    )
    assert await not_due.run_once() == []

    due = RuntimeSourcePollingScheduler(
        session_factory=session_factory,
        now_factory=lambda: _T0 + datetime.timedelta(seconds=90),
    )
    assert len(await due.run_once()) == 1


async def test_disabled_source_is_not_polled(session_factory):
    source_id = await _create_mock_source(session_factory, project_id="project-a")
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        await repo.update_source_config(source_id, None, RuntimeSourceUpdate(enabled=False))
        await session.commit()

    scheduler = RuntimeSourcePollingScheduler(session_factory=session_factory, now_factory=lambda: _T0)
    assert await scheduler.run_once() == []


async def test_run_once_skips_when_lock_is_already_held(session_factory):
    scheduler = RuntimeSourcePollingScheduler(session_factory=session_factory, now_factory=lambda: _T0)
    await scheduler._lock.acquire()
    try:
        assert await scheduler.run_once() == []
    finally:
        scheduler._lock.release()
