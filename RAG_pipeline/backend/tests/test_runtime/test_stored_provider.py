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
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeSyncStatusInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeConnection,
    RuntimeEstateOverview,
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


class TestScheduleProfileTool:
    """The schedule profile is the only rollup that converts into a named timezone,
    so its provider adapter has one extra job: no stdlib or SQLAlchemy exception may
    escape as anything other than RuntimeProviderError."""

    async def _seed_night_runs(self, session, project_id, job_name, count):
        repo = RuntimeStoreRepository(session)
        base = datetime.datetime(2026, 1, 5, 22, 30, tzinfo=UTC)
        for index in range(count):
            await repo.upsert_execution(
                project_id,
                RuntimeExecution(
                    execution_id=f"{project_id}-{job_name}-{index}",
                    job_name=f"{job_name}-{index}",
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=base + datetime.timedelta(days=index),
                    ended_at=base + datetime.timedelta(days=index, seconds=30),
                    duration_ms=30_000,
                ),
                "src-1",
                _T0,
            )
        await session.flush()

    async def test_is_clamped_to_its_own_agent_maximum_not_the_ranking_default(
        self, db_session
    ):
        """A schedule read is an inventory, so it gets a larger page than a ranking.

        It is still clamped -- an unbounded rollup can return an estate no
        context can hold -- but at its own number. Measured on live data: 67
        jobs in a 22:00-07:00 window against the ranking default of 25 made the
        agent slice the window into nine hourly calls, costing nine model steps
        and nine re-prefills to produce 172 rows for 67 jobs.
        """

        from src.runtime.models import ListRuntimeJobScheduleProfileInput
        from src.runtime.providers.stored import AGGREGATE_MAX_ROWS, aggregate_row_cap

        cap = aggregate_row_cap("list_runtime_job_schedule_profile")
        assert cap > AGGREGATE_MAX_ROWS, "an inventory rollup needs more than a ranking"

        await self._seed_night_runs(db_session, "p1", "night", cap + 5)
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        rows = await provider.list_runtime_job_schedule_profile(
            ListRuntimeJobScheduleProfileInput(start_hour=22, end_hour=7, limit=500), ctx
        )
        assert len(rows) == cap

    async def test_scope_is_passed_through_not_taken_from_arguments(self, db_session):
        from src.runtime.models import ListRuntimeJobScheduleProfileInput

        await self._seed_night_runs(db_session, "p1", "night", 2)
        await self._seed_night_runs(db_session, "p2", "night", 2)
        provider = StoredRuntimeProvider()
        scoped_ctx = RuntimeProviderContext(project_scope=["p1"], db=db_session)
        leaked = await provider.list_runtime_job_schedule_profile(
            ListRuntimeJobScheduleProfileInput(project_id="p2", start_hour=22, end_hour=7),
            scoped_ctx,
        )
        assert leaked == []
        allowed = await provider.list_runtime_job_schedule_profile(
            ListRuntimeJobScheduleProfileInput(start_hour=22, end_hour=7), scoped_ctx
        )
        assert {row.project_id for row in allowed} == {"p1"}

    async def test_requires_a_db_session(self):
        from src.runtime.models import ListRuntimeJobScheduleProfileInput

        provider = StoredRuntimeProvider()
        with pytest.raises(RuntimeProviderError):
            await provider.list_runtime_job_schedule_profile(
                ListRuntimeJobScheduleProfileInput(), None
            )

    async def test_unresolvable_timezone_surfaces_as_a_provider_error(self, db_session):
        """The input model rejects an unknown zone, so this can only be reached by
        bypassing validation -- which is exactly when a raw ZoneInfoNotFoundError
        would escape the provider boundary as an unhandled 500."""
        from src.runtime.models import ListRuntimeJobScheduleProfileInput

        params = ListRuntimeJobScheduleProfileInput.model_construct(
            timezone="Mars/Olympus_Mons", limit=10, offset=0, start_hour=None, end_hour=None
        )
        provider = StoredRuntimeProvider()
        ctx = RuntimeProviderContext(project_scope=None, db=db_session)
        with pytest.raises(RuntimeProviderError, match="Unusable timezone"):
            await provider.list_runtime_job_schedule_profile(params, ctx)


#: A minimal estate overview so `list_runtime_unhealthy` can assemble its summary
#: while every sub-read below it is recorded rather than executed. The numbers are
#: irrelevant here -- this file's subject is which arguments reach the repository.
_ESTATE_OVERVIEW = RuntimeEstateOverview(
    project_id="proj-reader",
    sources_total=0,
    sources_failed=0,
    sources_partial=0,
    sources_demo=0,
    engines_total=0,
    engines_online=0,
    engines_degraded=0,
    engines_offline=0,
    engines_unknown=0,
    environments_total=0,
    workspaces_total=0,
)


class TestStoredProviderCarriesTheReadSet:
    """The provider is the bridge between a resolved read and the repository.

    Most methods hand the params model straight through and the repository lifts
    `read_terms` off it. FOUR do not -- `list_sync_states`, `list_health_summaries`,
    `list_sources` and `search_log_events` take their filters as keywords -- and
    a fifth, `list_runtime_unhealthy`, BUILDS four sub-inputs of its own. Those
    five are where a read set gets dropped while `context.project_scope` stays
    widened, which returns the grantor's rows with the grant's environment and
    window limits silently unapplied.

    These tests are deliberately about the handoff rather than the SQL: the
    predicates themselves are pinned in `tests/test_runtime/test_read_scope.py`,
    and asserting them twice would mean two places to update when one changes.
    """

    @staticmethod
    def _terms():
        from src.runtime.read_scope import RuntimeReadTerm

        return [
            RuntimeReadTerm(project_id="proj-reader"),
            RuntimeReadTerm(project_id="proj-owner", environment_ids=["env-prod"]),
        ]

    def _record(self, monkeypatch, method_name, returns=None):
        seen = {}

        async def recorded(_self, scope, *args, **kwargs):  # noqa: ANN001
            seen.setdefault("calls", []).append(
                {"scope": scope, "args": args, "kwargs": kwargs}
            )
            return [] if returns is None else returns

        monkeypatch.setattr(
            f"src.runtime.store.repository.RuntimeStoreRepository.{method_name}",
            recorded,
        )
        return seen

    @pytest.mark.parametrize(
        ("provider_method", "repository_method", "input_factory"),
        [
            (
                "list_runtime_sync_status",
                "list_sync_states",
                lambda terms: ListRuntimeSyncStatusInput(read_terms=terms),
            ),
            (
                "list_runtime_health",
                "list_health_summaries",
                lambda terms: ListRuntimeHealthInput(read_terms=terms),
            ),
            (
                "list_runtime_sources",
                "list_sources",
                lambda terms: ListRuntimeInventoryInput(read_terms=terms),
            ),
        ],
    )
    async def test_a_keyword_repository_call_receives_the_read_set(
        self, monkeypatch, db_session, provider_method, repository_method, input_factory
    ):
        terms = self._terms()
        seen = self._record(monkeypatch, repository_method)
        ctx = RuntimeProviderContext(
            project_scope=["proj-reader", "proj-owner"], db=db_session
        )

        await getattr(StoredRuntimeProvider(), provider_method)(input_factory(terms), ctx)

        call = seen["calls"][0]
        # Both halves, in the same call. The widened scope alone is the leak.
        assert call["scope"] == ["proj-reader", "proj-owner"]
        assert list(call["kwargs"]["read_terms"]) == terms

    async def test_search_logs_takes_the_read_set_as_an_explicit_keyword(
        self, monkeypatch, db_session
    ):
        """Not a field on `SearchLogsInput`, and that is the point.

        `SearchLogsInput` is the model the runtime tool registry validates
        LLM-supplied arguments against. A `read_terms` field on it would put the
        read set inside a schema the model can write to, which is an
        authorization bypass rather than a convenience.
        """
        from src.runtime.registry import SearchLogsInput

        terms = self._terms()
        seen = self._record(monkeypatch, "search_log_events")
        ctx = RuntimeProviderContext(
            project_scope=["proj-reader", "proj-owner"], db=db_session
        )

        await StoredRuntimeProvider().search_logs(
            SearchLogsInput(question="timeout"), ctx, read_terms=terms
        )

        assert "read_terms" not in SearchLogsInput.model_fields
        assert list(seen["calls"][0]["kwargs"]["read_terms"]) == terms

    async def test_search_logs_without_a_read_set_keeps_its_previous_predicate(
        self, monkeypatch, db_session
    ):
        """The default exists for callers that never widen (`src/runtime/client.py`
        runs one project's tool call under that project's own scope). An empty
        read set is "nothing was resolved", which the repository reads as a
        fallback to the single `project_id` predicate -- not as a denial."""
        from src.runtime.registry import SearchLogsInput

        seen = self._record(monkeypatch, "search_log_events")
        ctx = RuntimeProviderContext(project_scope=["proj-reader"], db=db_session)

        await StoredRuntimeProvider().search_logs(
            SearchLogsInput(question="timeout", project_id="proj-reader"), ctx
        )

        assert list(seen["calls"][0]["kwargs"]["read_terms"]) == []
        assert seen["calls"][0]["kwargs"]["project_id"] == "proj-reader"

    async def test_the_unhealthy_summary_applies_the_grant_to_all_five_sub_reads(
        self, monkeypatch, db_session
    ):
        """One read that fans out into five, so it can half-apply a grant.

        A summary whose engine list obeys the grant and whose job list does not
        is worse than either alone: nothing in the result distinguishes them, so
        the leaked half is presented with the same confidence as the sound half.
        """
        terms = self._terms()
        recorded = {
            "get_estate_overview": self._record(
                monkeypatch,
                "get_estate_overview",
                returns=_ESTATE_OVERVIEW,
            ),
            **{
                name: self._record(monkeypatch, name)
                for name in (
                    "list_sources",
                    "list_engine_health",
                    "list_job_health",
                    "list_plan_health",
                )
            },
        }
        ctx = RuntimeProviderContext(
            project_scope=["proj-reader", "proj-owner"], db=db_session
        )

        await StoredRuntimeProvider().list_runtime_unhealthy(
            ListRuntimeInventoryInput(read_terms=terms), ctx
        )

        # `list_sources` takes the read set as a keyword; the other four receive
        # a params model, and three of those models are BUILT inside the method.
        assert list(recorded["list_sources"]["calls"][0]["kwargs"]["read_terms"]) == terms
        for name in ("get_estate_overview", "list_engine_health", "list_job_health", "list_plan_health"):
            params = recorded[name]["calls"][0]["args"][0]
            assert params.read_terms == terms, name
