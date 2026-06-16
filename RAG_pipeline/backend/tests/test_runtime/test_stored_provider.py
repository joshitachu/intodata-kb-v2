"""Unit tests for the DB-backed stored runtime provider (src/runtime/providers/stored.py).

Exercises the provider directly against a seeded repository: scope passthrough, the
required DB session, and not-found -> RuntimeProviderError mapping. End-to-end scope
enforcement through execute_runtime_tool is covered in test_client.py.
"""

import datetime
from datetime import UTC

import pytest

from src.runtime.models import (
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.stored import StoredRuntimeProvider
from src.runtime.store import RuntimeStoreRepository

_T0 = datetime.datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_execution(session, project_id, execution_id="e1", project_name="demo"):
    repo = RuntimeStoreRepository(session)
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name="nightly_sync",
            project_name=project_name,
            state=RuntimeExecutionState.SUCCESS,
            started_at=_T0,
            ended_at=_T0,
            duration_ms=1000,
        ),
        "mock",
        _T0,
    )


class TestStoredProviderContext:
    async def test_missing_db_raises_provider_error(self):
        provider = StoredRuntimeProvider()
        with pytest.raises(RuntimeProviderError):
            await provider.list_executions(
                ListExecutionsInput(), RuntimeProviderContext(project_scope=None, db=None)
            )

    async def test_none_context_raises_provider_error(self):
        provider = StoredRuntimeProvider()
        with pytest.raises(RuntimeProviderError):
            await provider.list_executions(ListExecutionsInput(), None)

    async def test_not_found_details_maps_to_provider_error(self, db_session):
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        with pytest.raises(RuntimeProviderError):
            await provider.get_execution_details(GetExecutionDetailsInput(execution_id="nope"), ctx)

    async def test_not_found_logs_maps_to_provider_error(self, db_session):
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        with pytest.raises(RuntimeProviderError):
            await provider.get_execution_logs(GetExecutionLogsInput(execution_id="nope"), ctx)


class TestStoredProviderScope:
    async def test_scoped_read_excludes_other_project(self, db_session):
        await _seed_execution(db_session, "project-a", "a1")
        await _seed_execution(db_session, "project-b", "b1")
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=["project-a"], db=db_session)
        result = await provider.list_executions(ListExecutionsInput(), ctx)
        assert {e.execution_id for e in result} == {"a1"}

    async def test_none_scope_reads_all_projects(self, db_session):
        await _seed_execution(db_session, "project-a", "a1")
        await _seed_execution(db_session, "project-b", "b1")
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        result = await provider.list_executions(ListExecutionsInput(), ctx)
        assert {e.execution_id for e in result} == {"a1", "b1"}
