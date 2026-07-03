"""Tests for the runtime sync scheduler."""

import asyncio
import datetime
from datetime import UTC

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.runtime.models import (
    ListRuntimeEnginesInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeSyncStatus,
)
from src.runtime.providers.mock import provider as mock_runtime_provider
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSyncBatch, RuntimeSyncScheduler, parse_project_ids

_NOW = datetime.datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


class _Adapter:
    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        return RuntimeSyncBatch(
            executions=[
                RuntimeExecution(
                    execution_id=f"{project_id}-exec",
                    job_name="orders",
                    project_name=project_id,
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=_NOW,
                )
            ],
            synced_at=_NOW,
        )


class _FailingAdapter:
    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        raise RuntimeError("token=secret-value source offline")


class _CountingAdapter:
    calls = 0

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        type(self).calls += 1
        return RuntimeSyncBatch(synced_at=_NOW)


def test_parse_project_ids():
    assert parse_project_ids(" a, b ,,c ") == ["a", "b", "c"]


async def test_run_once_executes_each_project(engine):
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_factory = lambda: session_maker()
    scheduler = RuntimeSyncScheduler(
        session_factory=session_factory,
        adapter_factory=_Adapter,
        source_key="fixture",
        project_ids=["project-a", "project-b"],
        interval_seconds=60,
    )

    results = await scheduler.run_once()

    assert [result.project_id for result in results] == ["project-a", "project-b"]
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        health = await repo.list_health_summaries(None)
        assert {item.project_id for item in health} == {"project-a", "project-b"}


async def test_run_once_skips_when_lock_is_already_held(engine):
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_factory = lambda: session_maker()
    scheduler = RuntimeSyncScheduler(
        session_factory=session_factory,
        adapter_factory=_Adapter,
        source_key="fixture",
        project_ids=["project-a"],
    )

    await scheduler._lock.acquire()
    try:
        assert await scheduler.run_once() == []
    finally:
        scheduler._lock.release()


async def test_run_once_records_adapter_failure_as_sync_status(engine):
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_factory = lambda: session_maker()
    scheduler = RuntimeSyncScheduler(
        session_factory=session_factory,
        adapter_factory=_FailingAdapter,
        source_key="fixture",
        project_ids=["project-a"],
    )

    results = await scheduler.run_once()

    assert len(results) == 1
    assert results[0].status == RuntimeSyncStatus.FAILED
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        states = await repo.list_sync_states(None, source_key="fixture")
        assert len(states) == 1
        assert states[0].status == RuntimeSyncStatus.FAILED
        assert states[0].error_summary == "<redacted> source offline"


async def test_run_once_can_use_mock_fixture_adapter(engine):
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_factory = lambda: session_maker()
    scheduler = RuntimeSyncScheduler(
        session_factory=session_factory,
        adapter_factory=lambda: mock_runtime_provider,
        source_key="mock",
        project_ids=["project-a"],
        interval_seconds=60,
    )

    results = await scheduler.run_once()

    assert results[0].status == RuntimeSyncStatus.SUCCESS
    assert results[0].sources_written == 1
    assert results[0].engines_written == 1
    assert results[0].jobs_written == 1
    assert results[0].job_runs_written == 2
    async with session_factory() as session:
        repo = RuntimeStoreRepository(session)
        sources = await repo.list_sources(None, source_key="mock", project_id="project-a")
        engines = await repo.list_engines(None, ListRuntimeEnginesInput(source_key="mock"))
        assert [source.label for source in sources] == ["Mock runtime source"]
        assert [engine.name for engine in engines] == ["mock-engine-01"]


async def test_background_scheduler_starts_and_stops(engine):
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session_factory = lambda: session_maker()
    _CountingAdapter.calls = 0
    scheduler = RuntimeSyncScheduler(
        session_factory=session_factory,
        adapter_factory=_CountingAdapter,
        source_key="fixture",
        project_ids=["project-a"],
    )

    task = scheduler.start_background()
    for _ in range(20):
        if _CountingAdapter.calls:
            break
        await asyncio.sleep(0.01)
    await scheduler.stop()

    assert task.done()
    assert scheduler.is_running is False
    assert _CountingAdapter.calls >= 1
