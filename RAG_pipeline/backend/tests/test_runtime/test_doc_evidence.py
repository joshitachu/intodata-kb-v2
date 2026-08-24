"""Repository tests for the documentation-evidence read path.

`list_doc_evidence` feeds an LLM prompt, so the properties under test are the
ones that make a generated document wrong rather than merely ugly:

* resolution must be EXACT -- werkorder really holds `AssetCheck_GetLocation`
  and `AssetCheck_GetLocations`, and attributing one job's failures to its
  sibling is a fabricated claim about production;
* latency must exclude runs that never succeeded -- verified in werkorder, a
  hung execution pushed p95 to 13 days;
* units with no runtime data must come back named, so a writer states "no
  runtime data" instead of inferring from silence that a job never runs;
* failure text must be redacted, because it is the only free text that crosses
  into the prompt.
"""

import datetime
from datetime import UTC

import pytest

from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanStep,
)
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime.store import RuntimeStoreRepository

_P = "proj-doc"
_OTHER = "proj-other"
_SRC = "tmc"
_T0 = datetime.datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
_SINCE = _T0 - datetime.timedelta(days=30)


async def _execution(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    state: str,
    started_at: datetime.datetime,
    duration_ms: int | None,
    ended: bool = True,
) -> None:
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started_at,
            ended_at=(
                started_at + datetime.timedelta(milliseconds=duration_ms or 0) if ended else None
            ),
            duration_ms=duration_ms,
        ),
        _SRC,
        _T0,
    )


@pytest.fixture
async def repo(db_session):
    """Seed two sibling jobs whose names differ by one character.

    GetLocation  : 4 successes (100/200/300/400 ms) + 1 success at 9_999_999 ms
                   that never ended, and 1 failure.
    GetLocations : 2 successes only -- the sibling that must never absorb
                   GetLocation's failure.
    """
    store = RuntimeStoreRepository(db_session)
    for index, duration in enumerate((100, 200, 300, 400)):
        await _execution(
            store,
            project_id=_P,
            execution_id=f"loc-ok-{index}",
            job_name="AssetCheck_GetLocation",
            state="success",
            started_at=_T0 + datetime.timedelta(hours=index),
            duration_ms=duration,
        )
    # Successful state but never completed: the shape that produced a 13-day p95.
    await _execution(
        store,
        project_id=_P,
        execution_id="loc-hung",
        job_name="AssetCheck_GetLocation",
        state="success",
        started_at=_T0 + datetime.timedelta(hours=5),
        duration_ms=9_999_999,
        ended=False,
    )
    await _execution(
        store,
        project_id=_P,
        execution_id="loc-fail",
        job_name="AssetCheck_GetLocation",
        state="failed",
        started_at=_T0 + datetime.timedelta(hours=6),
        duration_ms=5_000_000,
    )
    for index, duration in enumerate((150, 250)):
        await _execution(
            store,
            project_id=_P,
            execution_id=f"locs-ok-{index}",
            job_name="AssetCheck_GetLocations",
            state="success",
            started_at=_T0 + datetime.timedelta(hours=index),
            duration_ms=duration,
        )
    await _execution(
        store,
        project_id=_OTHER,
        execution_id="other-1",
        job_name="AssetCheck_GetLocation",
        state="failed",
        started_at=_T0,
        duration_ms=999,
    )
    await store.upsert_failure(
        _P,
        RuntimeFailure(
            execution_id="loc-fail",
            job_name="AssetCheck_GetLocation",
            failed_at=_T0 + datetime.timedelta(hours=6),
            error_summary="Connect failed for user admin password=hunter2 at db.internal",
        ),
        _SRC,
        _T0,
    )
    await store.upsert_job_run(
        _P,
        RuntimeJobRun(
            run_id="run-1",
            job_id="job-loc",
            job_name="AssetCheck_GetLocation",
            source_key=_SRC,
            state=RuntimeExecutionState.SUCCESS,
            environment_name="PROD",
            started_at=_T0,
        ),
        _SRC,
        _T0,
    )
    await store.upsert_plan(
        _P,
        RuntimePlan(
            plan_id="plan-1",
            source_key=_SRC,
            name="Nightly_Locations",
            steps=[
                RuntimePlanStep(
                    step_id="s1", name="step", job_id="job-loc", job_name="AssetCheck_GetLocation"
                )
            ],
        ),
        _SRC,
        _T0,
    )
    return store


async def test_sibling_names_are_never_confused(repo):
    """`AssetCheck_GetLocation`'s failure must not reach `..._GetLocations`."""
    units, _ = await repo.list_doc_evidence(
        None,
        project_id=_P,
        unit_names=["AssetCheck_GetLocation", "AssetCheck_GetLocations"],
        since=_SINCE,
    )
    by_unit = {unit.unit_name: unit for unit in units}
    assert by_unit["AssetCheck_GetLocation"].failed_runs == 1
    assert by_unit["AssetCheck_GetLocations"].failed_runs == 0
    assert by_unit["AssetCheck_GetLocations"].total_runs == 2
    assert by_unit["AssetCheck_GetLocations"].recent_failures == []


async def test_latency_excludes_unfinished_and_failed_runs(repo):
    """p95 reflects completed successes only, not a hung or failed run."""
    units, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    unit = units[0]
    # 100/200/300/400 are the completed successes; the 9_999_999 ms hung run and
    # the 5_000_000 ms failure must not appear in any percentile.
    assert unit.max_duration_ms == 400
    assert unit.p50_duration_ms in {200, 300}
    assert unit.p95_duration_ms == 400
    # The hung run still counts as activity, so the run totals keep it.
    assert unit.total_runs == 6
    assert unit.unfinished_runs == 1


async def test_units_without_runtime_data_are_reported(repo):
    units, unmatched = await repo.list_doc_evidence(
        None,
        project_id=_P,
        unit_names=["AssetCheck_GetLocation", "NeverRan_Job"],
        since=_SINCE,
    )
    assert [unit.unit_name for unit in units] == ["AssetCheck_GetLocation"]
    assert unmatched == ["NeverRan_Job"]


async def test_failure_text_is_redacted(repo):
    units, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    summary = units[0].recent_failures[0].error_summary
    assert "hunter2" not in summary
    assert "<redacted>" in summary


async def test_repeated_failures_group_into_one_mode_with_a_count(repo, db_session):
    """The same error under different execution ids is ONE failure mode.

    werkorder's `RHM2Smartflow_Workorders` fails the same way hundreds of times;
    listing each occurrence would bury every rarer failure.
    """
    store = RuntimeStoreRepository(db_session)
    for index in range(4):
        await _execution(
            store,
            project_id=_P,
            execution_id=f"rep-{index}",
            job_name="AssetCheck_GetLocations",
            state="failed",
            started_at=_T0 + datetime.timedelta(hours=10 + index),
            duration_ms=100,
        )
        await store.upsert_failure(
            _P,
            RuntimeFailure(
                execution_id=f"rep-{index}",
                job_name="AssetCheck_GetLocations",
                failed_at=_T0 + datetime.timedelta(hours=10 + index),
                # Same failure; only the execution id and a counter vary.
                error_summary=(
                    f"Execution 8483f2c1-a51e-4249-b647-89678{index}c788d6 failed with "
                    f"code 1 in component tMSSqlConnection_2 after {1000 + index} ms"
                ),
            ),
            _SRC,
            _T0,
        )
    await store.upsert_failure(
        _P,
        RuntimeFailure(
            execution_id="rep-distinct",
            job_name="AssetCheck_GetLocations",
            failed_at=_T0 + datetime.timedelta(hours=9),
            error_summary="Remote engine was stopped/restarted",
        ),
        _SRC,
        _T0,
    )
    units, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocations"], since=_SINCE
    )
    failures = units[0].recent_failures
    assert len(failures) == 2, "four identical failures must collapse to one mode"
    assert failures[0].occurrences == 4
    assert "tMSSqlConnection_2" in failures[0].error_summary
    assert failures[1].occurrences == 1
    assert failures[1].error_summary == "Remote engine was stopped/restarted"


async def test_environments_and_plans_are_attached(repo):
    units, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    assert units[0].environments == ["PROD"]
    assert units[0].plans == ["Nightly_Locations"]
    assert units[0].resolved_by == "exact_name"


async def test_scan_map_override_wins_over_name_equality(repo, db_session):
    """A curated mapping resolves a unit whose name does not match the runtime job.

    The row is inserted directly: `upsert_job_scan_map` additionally demands a
    static topology snapshot, which is the publisher's contract and not what
    this read path is being tested for.
    """
    db_session.add(
        RuntimeJobScanMapRow(
            project_id=_P,
            source_key=_SRC,
            runtime_job_id="job-loc",
            static_job_id="LegacyLocationUnit",
            created_at=_T0,
            created_by="test",
        )
    )
    await db_session.flush()
    units, unmatched = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["LegacyLocationUnit"], since=_SINCE
    )
    assert unmatched == []
    assert units[0].unit_name == "LegacyLocationUnit"
    assert units[0].job_name == "AssetCheck_GetLocation"
    assert units[0].resolved_by == "scan_map"


async def test_other_projects_never_leak(repo):
    """proj-other ran the same job name; its failure must stay out."""
    units, _ = await repo.list_doc_evidence(
        [_P], project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    assert units[0].failed_runs == 1


async def test_empty_scope_denies_everything(repo):
    units, unmatched = await repo.list_doc_evidence(
        [], project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    assert units == []
    assert unmatched == ["AssetCheck_GetLocation"]


async def test_runs_per_day_needs_a_measurable_span(repo):
    """A cadence claim needs more than one run inside a single day."""
    units, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocations"], since=_SINCE
    )
    assert units[0].runs_per_day is None
