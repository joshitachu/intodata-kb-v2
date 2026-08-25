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
  into the prompt;
* the RUNTIME side of the mismatch must be reported too -- a project bound to
  the wrong scan directory matches one unit out of ten and publishes that as
  the whole picture unless the orphan jobs come back with it.
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
from src.db.models.runtime_data import RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeExecutionSummaryRow, RuntimeIncidentRow
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
    units, _, _ = await repo.list_doc_evidence(
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
    units, _, _ = await repo.list_doc_evidence(
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
    units, unmatched, _ = await repo.list_doc_evidence(
        None,
        project_id=_P,
        unit_names=["AssetCheck_GetLocation", "NeverRan_Job"],
        since=_SINCE,
    )
    assert [unit.unit_name for unit in units] == ["AssetCheck_GetLocation"]
    assert unmatched == ["NeverRan_Job"]


async def test_failure_text_is_redacted(repo):
    units, _, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    summary = units[0].recent_failures[0].error_summary
    assert "hunter2" not in summary
    assert "<redacted>" in summary


async def test_log_quality_and_useful_incident_are_indexed_without_raw_logs(repo, db_session):
    """Generation receives a bounded diagnosis, never the execution's log stream."""
    db_session.add_all(
        [
            RuntimeLogEventRow(
                project_id=_P,
                execution_id="loc-fail",
                timestamp=_T0 + datetime.timedelta(hours=6),
                severity="error",
                message="raw diagnostic line that must not cross the boundary",
                line_ordinal=1,
                event_fingerprint="f" * 64,
                source_key=_SRC,
                ingested_at=_T0,
            ),
            RuntimeExecutionSummaryRow(
                project_id=_P,
                execution_id="loc-fail",
                job_name="AssetCheck_GetLocation",
                error_count=1,
                failed_component="tMSSqlInput_1",
                primary_error_code="1205",
                primary_exception_class="java.sql.SQLException",
                causal_message="Transaction was chosen as the deadlock victim",
            ),
            RuntimeIncidentRow(
                project_id=_P,
                incident_id="incident-loc-fail",
                execution_id="loc-fail",
                signature_hash="d" * 64,
                symptom_summary="SQL Server deadlock victim",
                root_cause_candidate="SQL Server selected this transaction as a deadlock victim.",
                causal_message="Transaction was chosen as the deadlock victim",
                termination_component="tDie_1",
                confidence="medium",
                cause_origin="deterministic",
                pattern_id="sqlserver.deadlock",
                first_bad_at=_T0 + datetime.timedelta(hours=6),
                next_checks=["Inspect the competing database transaction"],
            ),
        ]
    )
    await db_session.flush()

    units, _, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    unit = units[0]
    assert unit.log_quality.failed_executions == 1
    assert unit.log_quality.executions_with_logs == 1
    assert unit.log_quality.diagnosed_executions == 1
    assert len(unit.incident_insights) == 1
    assert unit.incident_insights[0].pattern_id == "sqlserver.deadlock"
    assert unit.incident_insights[0].failed_component == "tMSSqlInput_1"
    assert unit.incident_insights[0].termination_component == "tDie_1"
    assert unit.incident_insights[0].next_checks == [
        "Inspect the competing database transaction"
    ]
    assert "raw diagnostic line" not in unit.model_dump_json()


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
    units, _, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocations"], since=_SINCE
    )
    failures = units[0].recent_failures
    assert len(failures) == 2, "four identical failures must collapse to one mode"
    assert failures[0].occurrences == 4
    assert "tMSSqlConnection_2" in failures[0].error_summary
    assert failures[1].occurrences == 1
    assert failures[1].error_summary == "Remote engine was stopped/restarted"


async def test_environments_and_plans_are_attached(repo):
    units, _, _ = await repo.list_doc_evidence(
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
    units, unmatched, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["LegacyLocationUnit"], since=_SINCE
    )
    assert unmatched == []
    assert units[0].unit_name == "LegacyLocationUnit"
    assert units[0].job_name == "AssetCheck_GetLocation"
    assert units[0].resolved_by == "scan_map"


async def test_other_projects_never_leak(repo):
    """proj-other ran the same job name; its failure must stay out."""
    units, _, _ = await repo.list_doc_evidence(
        [_P], project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    assert units[0].failed_runs == 1


async def test_empty_scope_denies_everything(repo):
    units, unmatched, _ = await repo.list_doc_evidence(
        [], project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    assert units == []
    assert unmatched == ["AssetCheck_GetLocation"]


async def test_runs_per_day_needs_a_measurable_span(repo):
    """A cadence claim needs more than one run inside a single day."""
    units, _, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocations"], since=_SINCE
    )
    assert units[0].runs_per_day is None


async def test_failure_text_loses_the_host_but_keeps_the_component(repo):
    """Entity-shape redaction on the path that actually reaches a prompt."""
    units, _, _ = await repo.list_doc_evidence(
        None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
    )
    summary = units[0].recent_failures[0].error_summary
    assert "db.internal" not in summary
    assert "<host>" in summary


# --- Orphan runtime jobs -----------------------------------------------------


class TestOrphanRuntimeJobs:
    """The runtime side of a mis-binding, which nothing reported before.

    Scan directory `alitalend` holds the AssetCheck estate whose units match,
    set for set, the runtime jobs filed under `reverse_engineer_VW`, while
    runtime `alitalend` holds a different 28-job workspace.  A doc run for
    `alitalend` matches 1 unit of 10 and, without this field, publishes that as
    the estate.
    """

    async def test_runtime_jobs_with_no_requested_unit_are_named(self, repo):
        _units, _unmatched, orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        assert orphans == ["AssetCheck_GetLocations"]

    async def test_a_fully_covered_request_reports_none(self, repo):
        _units, _unmatched, orphans = await repo.list_doc_evidence(
            None,
            project_id=_P,
            unit_names=["AssetCheck_GetLocation", "AssetCheck_GetLocations"],
            since=_SINCE,
        )
        assert orphans == []

    async def test_zero_matches_still_reports_the_orphans(self, repo):
        """The binding-suspect case: zero matches is where the field matters most."""
        units, unmatched, orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["SomeOtherEstate_Job"], since=_SINCE
        )
        assert units == []
        assert unmatched == ["SomeOtherEstate_Job"]
        assert orphans == ["AssetCheck_GetLocation", "AssetCheck_GetLocations"]

    async def test_batches_merge_by_intersection_not_union(self, repo):
        """A unit in batch 2 must not make its job look orphaned overall.

        Union over the two batches would claim BOTH jobs are orphans, which is
        the exact false alarm that would make a doc run report a wrong binding
        for a correctly bound project.
        """
        _u1, _m1, batch_one = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        _u2, _m2, batch_two = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocations"], since=_SINCE
        )
        assert set(batch_one) | set(batch_two) == {
            "AssetCheck_GetLocation",
            "AssetCheck_GetLocations",
        }, "union is the WRONG merge and this pins why"
        assert set(batch_one) & set(batch_two) == set()

    async def test_other_projects_runtime_jobs_are_never_orphans_here(self, repo, db_session):
        """`proj-other` runs a job of its own; it belongs to no request for `proj-doc`."""
        store = RuntimeStoreRepository(db_session)
        await _execution(
            store,
            project_id=_OTHER,
            execution_id="other-unique",
            job_name="OtherProject_OnlyJob",
            state="success",
            started_at=_T0,
            duration_ms=10,
        )
        _units, _unmatched, orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        assert "OtherProject_OnlyJob" not in orphans

    async def test_the_list_is_bounded(self, repo, monkeypatch):
        """A large mis-bound workspace must not become an unbounded payload."""
        from src.runtime.store import repository as repository_module

        monkeypatch.setattr(repository_module, "_DOC_EVIDENCE_MAX_ORPHAN_JOBS", 1)
        _units, _unmatched, orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["NoSuchUnit"], since=_SINCE
        )
        assert orphans == ["AssetCheck_GetLocation"], "sorted by name, so the cap is a prefix"

    async def test_denied_scope_reports_nothing(self, repo):
        _units, _unmatched, orphans = await repo.list_doc_evidence(
            [], project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        assert orphans == []


# --- Plan resolution ---------------------------------------------------------


class TestPlanResolution:
    """Plans resolve through `steps[].job_name`, and that is the measured best.

    Resolving through `steps[].job_id -> runtime_jobs.job_id` was proposed
    because TMC leaves most step names null.  Measured over the whole live
    mirror it recovers nothing: every null-name step carries a `job_id` that is
    absent from `runtime_jobs` (219 of 343 steps), so the id path resolves
    16/60/1/2 distinct jobs against the name path's 16/64/1/2 and their union
    adds zero.  These tests pin that shape so the change is not re-attempted
    from the null-name observation alone.
    """

    async def test_a_named_step_attaches_its_plan(self, repo):
        units, _unmatched, _orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        assert units[0].plans == ["Nightly_Locations"]

    async def test_a_null_name_step_points_at_a_job_the_mirror_does_not_hold(
        self, repo, db_session
    ):
        """The live shape of an unresolvable step, end to end.

        The step names no job and its `job_id` has no `runtime_jobs` row, so
        there is nothing to resolve it against by either key -- the gap is
        mirror coverage, not the choice of lookup column.
        """
        store = RuntimeStoreRepository(db_session)
        await store.upsert_plan(
            _P,
            RuntimePlan(
                plan_id="plan-unresolvable",
                source_key=_SRC,
                name="RHM2SmartFlow_all",
                steps=[
                    RuntimePlanStep(
                        step_id="s-null",
                        # The provider copies the id into `name` when it has no
                        # job name, which is the tell that it never had one.
                        name="655de702da9b600cea7fad19",
                        job_id="655de702da9b600cea7fad19",
                        job_name=None,
                    )
                ],
            ),
            _SRC,
            _T0,
        )
        units, _unmatched, _orphans = await repo.list_doc_evidence(
            None, project_id=_P, unit_names=["AssetCheck_GetLocation"], since=_SINCE
        )
        assert units[0].plans == ["Nightly_Locations"]
