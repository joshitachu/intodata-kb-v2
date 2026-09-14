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

        # A misfired and a timed-out run each make their job -- and the engine
        # hosting it -- something a human should look at, even though neither
        # is a FAILED run.
        assert result.overview.engines_unhealthy == 2
        assert result.overview.jobs_unhealthy == 3
        assert result.overview.plans_unhealthy == 1
        assert result.sources[0].last_synced_at == _T0
        assert {engine.engine_name for engine in result.unhealthy_engines} == {
            "remote-engine-prod-01",
            "remote-engine-prod-02",
        }
        assert {job.job_name for job in result.unhealthy_jobs} == {
            "esb_order_sync_route",
            "esb_inventory_sync_task",
            "esb_nightly_reconcile_batch",
        }
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


class TestAggregateRollupTools:
    """The rollup tools share input models with the dashboard, whose page caps are far
    larger than a tool result should be. The provider must clamp regardless of the
    arguments a model supplies, and must still pass scope straight through."""

    async def _seed_runs(self, session, project_id, count):
        repo = RuntimeStoreRepository(session)
        for index in range(count):
            await repo.upsert_execution(
                project_id,
                RuntimeExecution(
                    execution_id=f"{project_id}-run-{index}",
                    job_name=f"job-{index}",
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=_T0 + datetime.timedelta(hours=index),
                    ended_at=_T0 + datetime.timedelta(hours=index),
                    duration_ms=index + 1,
                ),
                "src-1",
                _T0,
            )
        await session.flush()

    async def test_job_reliability_is_clamped_to_agent_maximum(self, db_session):
        from src.runtime.providers.stored import _AGGREGATE_MAX_ROWS
        from src.runtime.models import ListRuntimeJobReliabilityInput

        await self._seed_runs(db_session, "p1", _AGGREGATE_MAX_ROWS + 10)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        rows = await provider.list_runtime_job_reliability(
            ListRuntimeJobReliabilityInput(limit=500), ctx
        )
        assert len(rows) == _AGGREGATE_MAX_ROWS

    async def test_trend_is_clamped_to_agent_maximum(self, db_session):
        from src.runtime.providers.stored import _TREND_MAX_POINTS
        from src.runtime.models import RuntimeTrendBucket, RuntimeTrendInput

        await self._seed_runs(db_session, "p1", _TREND_MAX_POINTS + 5)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        points = await provider.get_runtime_outcome_trend(
            RuntimeTrendInput(bucket=RuntimeTrendBucket.HOUR, limit=400), ctx
        )
        assert len(points) == _TREND_MAX_POINTS

    async def test_scope_is_passed_through_not_taken_from_arguments(self, db_session):
        from src.runtime.models import ListRuntimeJobReliabilityInput

        await self._seed_runs(db_session, "p1", 2)
        await self._seed_runs(db_session, "p2", 2)
        provider = StoredRuntimeProvider()
        scoped_ctx = RuntimeProviderContext(project_scope=["p1"], db=db_session)
        # project_id is an in-scope filter, never an authorization source: asking for
        # p2 while scoped to p1 must return nothing rather than p2's rows.
        rows = await provider.list_runtime_job_reliability(
            ListRuntimeJobReliabilityInput(project_id="p2", limit=50), scoped_ctx
        )
        assert rows == []
        allowed = await provider.list_runtime_job_reliability(
            ListRuntimeJobReliabilityInput(limit=50), scoped_ctx
        )
        assert {row.project_id for row in allowed} == {"p1"}

    async def test_rollup_tools_require_a_db_session(self):
        from src.runtime.models import ListRuntimeErrorSignaturesInput

        provider = StoredRuntimeProvider()
        with pytest.raises(RuntimeProviderError):
            await provider.list_runtime_error_signatures(
                ListRuntimeErrorSignaturesInput(), None
            )
