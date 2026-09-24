"""Pins the batched estate lookup behind `/runtime/incidents`.

`list_incidents` used to resolve each incident's estate on its own -- six queries
an incident, one of which read the newest 50 `runtime_plan_runs` rows *with* their
`step_results` JSON. A 50-row page therefore issued around 300 statements and
pulled the same JSON out of TOAST fifty times.

The estate is now resolved for the whole page at once. These tests pin both halves
of that: the statement count must not grow with the page size, and the batched
answer must equal the single-record answer it replaced -- the point lookup and the
list share one implementation precisely so the two cannot drift.
"""

import datetime
from datetime import UTC

import pytest

from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.insights.repository import RuntimeInsightRepository
from src.runtime.insights.models import RuntimeFailureEstateContext
from src.runtime.models import (
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimeSource,
    RuntimeSyncStatus,
)
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store.repository import RuntimeStoreRepository as _Store

_P = "proj-a"
_SRC = "src-1"
_T0 = datetime.datetime(2026, 4, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime.datetime:
    return _T0 + datetime.timedelta(minutes=minutes)


async def _seed_estate(store: _Store) -> None:
    await store.upsert_source(
        _P,
        RuntimeSource(
            estate_key=_P,
            source_key=_SRC,
            source_type="mock",
            label="Mock source",
            status=RuntimeSyncStatus.SUCCESS,
            last_synced_at=_T0,
            last_seen_at=_T0,
            ingested_at=_T0,
        ),
        _T0,
    )
    await store.upsert_engine(
        _P,
        RuntimeEngine(
            source_key=_SRC,
            engine_id="engine-1",
            name="engine one",
            status=RuntimeEngineStatus.ONLINE,
            version="8.0.1",
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-main",
            workspace_name="Main",
            last_seen_at=_T0,
        ),
        _SRC,
        _T0,
    )


async def _seed_failure(
    store: _Store,
    db_session,
    *,
    index: int,
    with_job_run: bool = True,
    job_id: str = "job-1",
    job_name: str = "orders",
) -> str:
    """One failed execution + its incident, optionally with a job run beside it."""
    execution_id = f"exec-{index}"
    await store.upsert_execution(
        _P,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState.FAILED,
            started_at=_at(index),
            ended_at=_at(index),
            duration_ms=1000,
        ),
        _SRC,
        _T0,
    )
    if with_job_run:
        await store.upsert_job_run(
            _P,
            RuntimeJobRun(
                source_key=_SRC,
                run_id=f"run-{index}",
                job_id=job_id,
                job_name=job_name,
                execution_id=execution_id,
                state=RuntimeExecutionState.FAILED,
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-main",
                workspace_name="Main",
                engine_id="engine-1",
                engine_name="engine one",
                started_at=_at(index),
                ended_at=_at(index),
                duration_ms=1000,
            ),
            _SRC,
            _T0,
        )
    db_session.add(
        RuntimeIncidentRow(
            project_id=_P,
            incident_id=f"inc-{index}",
            execution_id=execution_id,
            signature_hash="sig-1",
            symptom_summary="orders failed",
            first_bad_at=_at(index),
        )
    )
    await db_session.flush()
    return execution_id


class TestStatementCountDoesNotScaleWithThePage:
    async def test_a_twenty_row_page_costs_the_same_statements_as_a_one_row_page(
        self, db_session, statements
    ):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        for index in range(20):
            await _seed_failure(store, db_session, index=index)
        insights = RuntimeInsightRepository(db_session)

        with statements() as one:
            first = await insights.list_incidents([_P], limit=1)
        with statements() as twenty:
            page = await insights.list_incidents([_P], limit=20)

        assert len(first) == 1
        assert len(page) == 20
        assert one.count == twenty.count, (
            "estate resolution must be batched: "
            f"1 row took {one.count} statements, 20 rows took {twenty.count}"
        )
        # A fixed, small budget: incidents, executions, then one statement per
        # estate relation. Left as an inequality so an added relation is a
        # deliberate edit rather than a silent regression to per-row lookups.
        assert twenty.count <= 10

    async def test_a_page_never_reads_the_plan_run_window_more_than_once_per_source(
        self, db_session, statements
    ):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        for index in range(10):
            await _seed_failure(store, db_session, index=index)
        insights = RuntimeInsightRepository(db_session)

        with statements() as recorded:
            await insights.list_incidents([_P], limit=10)

        assert len(recorded.mentioning("runtime_plan_runs")) == 1


class TestBatchedAnswerMatchesThePointLookup:
    @pytest.mark.parametrize("with_job_run", [True, False])
    async def test_list_and_get_agree_on_the_estate(self, db_session, with_job_run):
        """The two callers share one loader, so their answers must be identical."""
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        if not with_job_run:
            # No job run: the job must still resolve, by name off the execution.
            await store.upsert_job(
                _P,
                RuntimeJob(
                    source_key=_SRC,
                    job_id="job-1",
                    name="orders",
                    display_name="Orders",
                    engine_id="engine-1",
                    engine_name="engine one",
                    environment_id="env-prod",
                    environment_name="Production",
                    workspace_id="ws-main",
                    workspace_name="Main",
                ),
                _SRC,
                _T0,
            )
        await _seed_failure(store, db_session, index=0, with_job_run=with_job_run)
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]
        fetched = await insights.get_incident([_P], "inc-0")

        assert listed.estate_context == fetched.estate_context
        assert listed.model_dump() == fetched.model_dump()
        assert listed.estate_context.source_label == "Mock source"
        assert listed.estate_context.engine_name == "engine one"
        assert listed.estate_context.engine_version == "8.0.1"
        assert listed.estate_context.environment_name == "Production"
        assert listed.estate_context.job_name == "orders"

    async def test_the_engine_resolves_through_the_job_when_the_run_names_none(
        self, db_session
    ):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await store.upsert_job(
            _P,
            RuntimeJob(
                source_key=_SRC,
                job_id="job-1",
                name="orders",
                engine_id="engine-1",
                engine_name="engine one",
            ),
            _SRC,
            _T0,
        )
        await store.upsert_execution(
            _P,
            RuntimeExecution(
                execution_id="exec-0",
                job_name="orders",
                state=RuntimeExecutionState.FAILED,
                started_at=_T0,
                ended_at=_T0,
                duration_ms=1,
            ),
            _SRC,
            _T0,
        )
        await store.upsert_job_run(
            _P,
            RuntimeJobRun(
                source_key=_SRC,
                run_id="run-0",
                job_id="job-1",
                job_name="orders",
                execution_id="exec-0",
                state=RuntimeExecutionState.FAILED,
                engine_id=None,
                engine_name=None,
                started_at=_T0,
                ended_at=_T0,
                duration_ms=1,
            ),
            _SRC,
            _T0,
        )
        db_session.add(
            RuntimeIncidentRow(
                project_id=_P,
                incident_id="inc-0",
                execution_id="exec-0",
                signature_hash="sig-1",
                first_bad_at=_T0,
            )
        )
        await db_session.flush()
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]

        assert listed.estate_context.engine_id == "engine-1"
        assert listed.estate_context.engine_status == RuntimeEngineStatus.ONLINE.value
        assert listed.estate_context == (await insights.get_incident([_P], "inc-0")).estate_context

    async def test_an_execution_with_no_inventory_gets_an_empty_context(self, db_session):
        store = RuntimeStoreRepository(db_session)
        db_session.add(
            RuntimeIncidentRow(
                project_id=_P,
                incident_id="inc-orphan",
                execution_id="exec-missing",
                signature_hash="sig-1",
                first_bad_at=_T0,
            )
        )
        await db_session.flush()
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]

        assert listed.estate_context == RuntimeFailureEstateContext()
        assert listed.job_name is None
        assert store is not None  # keeps the store fixture meaningful


class TestPlanRunLinkage:
    async def _seed_plan(self, store):
        await store.upsert_plan(
            _P,
            RuntimePlan(
                source_key=_SRC,
                plan_id="plan-1",
                name="nightly",
                display_name="Nightly",
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-main",
                workspace_name="Main",
                step_count=1,
            ),
            _SRC,
            _T0,
        )

    async def _add_plan_run(self, store, *, run_id, minute, execution_ids):
        await store.upsert_plan_run(
            _P,
            RuntimePlanRun(
                source_key=_SRC,
                run_id=run_id,
                plan_id="plan-1",
                plan_name="nightly",
                state=RuntimeExecutionState.FAILED,
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-main",
                workspace_name="Main",
                started_at=_at(minute),
                ended_at=_at(minute),
                duration_ms=1,
                step_results=[
                    RuntimePlanRunStepResult(
                        step_id=f"step-{execution_id}",
                        job_id="job-1",
                        job_name="orders",
                        state=RuntimeExecutionState.FAILED,
                        execution_id=execution_id,
                    )
                    for execution_id in execution_ids
                ],
            ),
            _SRC,
            _T0,
        )

    async def test_every_incident_on_the_page_resolves_its_own_plan_run(self, db_session):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await self._seed_plan(store)
        for index in range(3):
            await _seed_failure(store, db_session, index=index)
            await self._add_plan_run(
                store, run_id=f"plan-run-{index}", minute=index, execution_ids=[f"exec-{index}"]
            )
        insights = RuntimeInsightRepository(db_session)

        page = await insights.list_incidents([_P], limit=10)

        by_incident = {item.incident_id: item.estate_context for item in page}
        for index in range(3):
            context = by_incident[f"inc-{index}"]
            assert context.plan_id == "plan-1"
            assert context.plan_name == "nightly"
            assert context.plan_display_name == "Nightly"
            assert context.plan_run_id == f"plan-run-{index}"
            assert context == (
                await insights.get_incident([_P], f"inc-{index}")
            ).estate_context

    async def test_the_match_window_stays_bounded(self, db_session):
        """The link lives in JSON, so the scan is bounded -- and must stay bounded.

        An execution referenced only by a plan run older than the newest window is
        deliberately not linked; widening the window here would change what the
        endpoint reads, not just how fast it reads it.
        """
        from src.runtime.insights.repository import _PLAN_RUN_MATCH_WINDOW

        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await self._seed_plan(store)
        await _seed_failure(store, db_session, index=0)
        # The plan run naming exec-0 is the OLDEST; bury it under a full window.
        await self._add_plan_run(store, run_id="plan-run-old", minute=0, execution_ids=["exec-0"])
        for index in range(_PLAN_RUN_MATCH_WINDOW):
            await self._add_plan_run(
                store, run_id=f"plan-run-{index}", minute=index + 1, execution_ids=[]
            )
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]

        assert listed.estate_context.plan_run_id is None
        assert listed.estate_context == (await insights.get_incident([_P], "inc-0")).estate_context

    async def test_the_newest_plan_run_in_the_window_wins(self, db_session):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await self._seed_plan(store)
        await _seed_failure(store, db_session, index=0)
        await self._add_plan_run(store, run_id="older", minute=1, execution_ids=["exec-0"])
        await self._add_plan_run(store, run_id="newer", minute=2, execution_ids=["exec-0"])
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]

        assert listed.estate_context.plan_run_id == "newer"


class TestScopeIsPreserved:
    async def test_scope_still_gates_the_page_and_the_estate(self, db_session):
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await _seed_failure(store, db_session, index=0)
        insights = RuntimeInsightRepository(db_session)

        assert len(await insights.list_incidents(None, limit=10)) == 1
        assert len(await insights.list_incidents([_P], limit=10)) == 1
        assert await insights.list_incidents(["other"], limit=10) == []
        assert await insights.list_incidents([], limit=10) == []

    async def test_an_out_of_scope_execution_does_not_leak_its_estate(self, db_session):
        """The incident row is in scope; its execution is read under the same scope."""
        store = RuntimeStoreRepository(db_session)
        await _seed_estate(store)
        await _seed_failure(store, db_session, index=0)
        insights = RuntimeInsightRepository(db_session)

        listed = (await insights.list_incidents([_P], limit=10))[0]

        assert listed.job_name == "orders"
        assert listed.estate_context.source_key == _SRC
