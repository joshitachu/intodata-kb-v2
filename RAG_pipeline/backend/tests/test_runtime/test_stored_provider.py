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
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeConnection,
    RuntimeJobDependency,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.stored import StoredRuntimeProvider
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store.seed import seed_rich_runtime_data

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


class TestStoredProviderEstateTools:
    async def test_lists_seeded_runtime_estate(self, db_session):
        await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_T0, build_insights=True)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=["esbmain"], db=db_session)

        sources = await provider.list_runtime_sources(ListRuntimeInventoryInput(), ctx)
        engines = await provider.list_runtime_engines(ListRuntimeEnginesInput(), ctx)
        jobs = await provider.list_runtime_jobs(ListRuntimeJobsInput(), ctx)
        plans = await provider.list_runtime_plans(ListRuntimePlansInput(), ctx)

        assert [source.source_key for source in sources] == ["mock"]
        assert {engine.name for engine in engines} >= {"remote-engine-prod-01", "remote-engine-prod-02"}
        assert {job.name for job in jobs} >= {"esb_order_sync_route", "esb_billing_export_route"}
        assert {plan.name for plan in plans} == {"nightly_order_flow"}

    async def test_list_runtime_unhealthy_returns_freshness_and_unhealthy_entities(self, db_session):
        await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_T0, build_insights=True)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=["esbmain"], db=db_session)

        result = await provider.list_runtime_unhealthy(ListRuntimeInventoryInput(), ctx)

        assert result.overview.engines_unhealthy == 1
        assert result.overview.jobs_unhealthy == 1
        assert result.overview.plans_unhealthy == 1
        assert result.sources[0].last_synced_at == _T0
        assert [engine.engine_name for engine in result.unhealthy_engines] == ["remote-engine-prod-02"]
        assert [job.job_name for job in result.unhealthy_jobs] == ["esb_order_sync_route"]
        assert [plan.plan_name for plan in result.unhealthy_plans] == ["nightly_order_flow"]

    async def test_estate_tool_arguments_cannot_widen_project_scope(self, db_session):
        await seed_rich_runtime_data(db_session, project_id="project-a", synced_at=_T0, build_insights=True)
        await seed_rich_runtime_data(db_session, project_id="project-b", synced_at=_T0, build_insights=True)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=["project-a"], db=db_session)

        result = await provider.list_runtime_engines(
            ListRuntimeEnginesInput(project_id="project-b"),
            ctx,
        )

        assert result == []

    async def test_lists_dependency_usage_from_db_facts(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_connection(
            "project-a",
            RuntimeConnection(
                source_key="mock",
                connection_id="conn-1",
                name="Oracle Orders",
                connection_type="oracle",
                workspace_id="ws-1",
                workspace_name="Analytics",
                owner="dba",
                last_seen_at=_T0,
                source_updated_at=_T0,
            ),
            "mock",
            _T0,
        )
        await repo.upsert_job_dependency(
            "project-a",
            RuntimeJobDependency(
                source_key="mock",
                job_id="job-1",
                job_name="nightly-load",
                dependency_type="connection",
                dependency_id="conn-1",
                dependency_name="Oracle Orders",
                dependency_kind="oracle",
                workspace_id="ws-1",
                workspace_name="Analytics",
                last_seen_at=_T0,
            ),
            "mock",
            _T0,
        )
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=["project-a"], db=db_session)

        connections = await provider.list_runtime_connections(ListRuntimeConnectionsInput(), ctx)
        usage = await provider.list_runtime_dependencies(
            ListRuntimeDependenciesInput(dependency_type="connection", dependency_id="conn-1"),
            ctx,
        )
        summary = await provider.get_runtime_dependency_summary(ListRuntimeInventoryInput(), ctx)

        assert [connection.name for connection in connections] == ["Oracle Orders"]
        assert [dependency.job_name for dependency in usage] == ["nightly-load"]
        assert summary.connections_total == 1
        assert summary.dependencies_total == 1
        assert "schedules" in summary.availability.unavailable
