"""Shared-tenant reads: one mirror, several documenting projects.

One Talend tenant commonly backs several scanned projects, and registering its
credential twice is refused on purpose -- that would mean a second poll and two
mirrors of one estate free to drift apart. A second project therefore reaches
the tenant through an explicit READ GRANT over the existing mirror.

The properties under test are the ones that decide whether a generated document
is true:

* the unshared path must be untouched -- it is the regression floor, and a
  single-project read still emits the `==` predicate it always did;
* a grant must widen the read, and only a grant -- no request parameter may;
* the insights tables carry no `source_key`, so their scope arrives purely
  through the join to the execution population; if that is wrong they leak;
* one job name can now exist under two project ids, and the reliability rollups
  must MERGE rather than let one silently overwrite the other.
"""

import datetime
from datetime import UTC

import pytest

from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.db.models.runtime_data import RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJobRun,
)
from src.runtime.store import RuntimeDuplicateSourceError, RuntimeStoreRepository

# OWNER holds the credential and pays the poll; READER documents the same
# tenant and owns no runtime source at all.
_OWNER = "proj-owner"
_READER = "proj-reader"
_STRANGER = "proj-stranger"
_SRC = "tmc"
_T0 = datetime.datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
_SINCE = _T0 - datetime.timedelta(days=30)


async def _execution(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    state: str = "success",
    started_at: datetime.datetime | None = None,
    duration_ms: int = 100,
) -> None:
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started_at or _T0,
            ended_at=(started_at or _T0) + datetime.timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
        ),
        _SRC,
        _T0,
    )


async def _job_run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    run_id: str,
    job_name: str,
    environment_id: str,
    environment_name: str,
    execution_id: str | None = None,
) -> None:
    await store.upsert_job_run(
        project_id,
        RuntimeJobRun(
            run_id=run_id,
            job_id=f"job-{job_name}",
            job_name=job_name,
            source_key=_SRC,
            state=RuntimeExecutionState.SUCCESS,
            environment_id=environment_id,
            environment_name=environment_name,
            started_at=_T0,
            execution_id=execution_id or run_id,
        ),
        _SRC,
        _T0,
    )


@pytest.fixture
async def store(db_session):
    """The owner's mirror: two jobs in PROD, one in ACC.

    Everything is stamped with `_OWNER`, because that is what the sync writer
    does -- the reader project owns none of these rows, which is precisely why
    it cannot see them without a grant.
    """
    repo = RuntimeStoreRepository(db_session)
    for index, job in enumerate(("Maximo2RHM_Workorders", "RHM2Smartflow_Workorders")):
        await _execution(
            repo,
            project_id=_OWNER,
            execution_id=f"prod-{index}",
            job_name=job,
            started_at=_T0 + datetime.timedelta(hours=index),
        )
        await _job_run(
            repo,
            project_id=_OWNER,
            run_id=f"prod-{index}",
            job_name=job,
            environment_id="env-prod",
            environment_name="PROD",
        )
    await _execution(
        repo,
        project_id=_OWNER,
        execution_id="acc-0",
        job_name="Acceptance_Only_Job",
        state="failed",
        started_at=_T0 + datetime.timedelta(hours=3),
    )
    await _job_run(
        repo,
        project_id=_OWNER,
        run_id="acc-0",
        job_name="Acceptance_Only_Job",
        environment_id="env-acc",
        environment_name="ACC",
    )
    return repo


async def _source(store: RuntimeStoreRepository, project_id: str = _OWNER):
    return await store.create_source_config(
        project_id=project_id, source_type="mock", label="Talend EU"
    )


class TestRegressionFloor:
    """A project with no grants must behave exactly as it did before."""

    async def test_read_set_of_an_ungranted_project_is_itself_alone(self, store):
        assert await store.resolve_runtime_read_projects(_OWNER) == [_OWNER]

    async def test_a_project_without_a_grant_reads_nothing_of_the_tenant(self, store):
        await _source(store)

        units, unmatched, _ = await store.list_doc_evidence(
            None,
            project_id=_STRANGER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=await store.resolve_runtime_read_projects(_STRANGER),
        )

        assert units == []
        assert unmatched == ["Maximo2RHM_Workorders"]

    async def test_the_owner_still_reads_its_own_mirror(self, store):
        units, _, _ = await store.list_doc_evidence(
            None,
            project_id=_OWNER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
        )

        assert [unit.unit_name for unit in units] == ["Maximo2RHM_Workorders"]


class TestGrantedRead:
    async def test_a_grant_lets_the_reader_see_the_tenant(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        read_projects = await store.resolve_runtime_read_projects(_READER)
        units, unmatched, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
        )

        assert read_projects == [_READER, _OWNER]
        assert [unit.unit_name for unit in units] == ["Maximo2RHM_Workorders"]
        assert unmatched == []

    async def test_a_grant_carries_its_own_environment_scope(self, store):
        """Two projects share a tenant BECAUSE they care about different parts.

        The reader documenting production must not inherit the owner's ACC
        traffic -- unscoped, the estate's failure rate describes no environment
        that exists.
        """
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=None,
            created_by="1",
        )

        scope = await store.resolve_doc_read_scope(
            await store.resolve_runtime_read_projects(_READER),
            _READER,
            default_window_days=90,
        )

        assert scope.environment_ids == ["env-prod"]
        assert scope.environment_names == ["PROD"]
        assert scope.configured is True

    async def test_the_environment_picker_offers_the_granted_tenant(self, store):
        """A picker that showed the reader an empty list would make its own
        selection resolve to no evidence, with no visible cause."""
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        offered = await store.list_mirrored_environments(
            read_projects, _READER, project_ids=read_projects
        )

        assert {item.environment_id for item in offered} == {"env-prod", "env-acc"}

    async def test_a_scoped_grant_excludes_the_other_environment(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        units, unmatched, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Acceptance_Only_Job"],
            since=_SINCE,
            environment_ids=["env-prod"],
            project_ids=read_projects,
        )

        assert units == []
        assert unmatched == ["Acceptance_Only_Job"]


class TestBlastRadius:
    async def test_a_grant_reports_the_projects_its_workspace_map_exposes(self, store):
        """A grant filters by project id, so it also reaches any project the
        source's workspace map re-points rows to. That has to be stated."""
        source = await _source(store)
        await store.add_container_mapping(
            source_id=source.id,
            container_key="workspace-b",
            container_name="Workspace B",
            project_id="proj-third",
            created_by="1",
        )

        grant = await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        assert grant.exposes_project_ids == [_OWNER, "proj-third"]
        assert await store.resolve_runtime_read_projects(_READER) == [
            _READER,
            _OWNER,
            "proj-third",
        ]

    async def test_granting_a_source_to_its_own_project_is_refused(self, store):
        """Ownership already implies read. A row for it could be revoked, which
        would read as revoking the owner's access to its own mirror."""
        source = await _source(store)

        with pytest.raises(RuntimeDuplicateSourceError):
            await store.add_read_grant(
                source_id=source.id,
                project_id=_OWNER,
                environment_ids=None,
                window_days=None,
                created_by="1",
            )

    async def test_deleting_the_source_revokes_its_grants(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        await store.delete_source_config(source.id, None)

        assert await store.resolve_runtime_read_projects(_READER) == [_READER]

    async def test_revoking_a_grant_narrows_the_read_back(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        await store.delete_read_grant(source.id, _READER)

        assert await store.resolve_runtime_read_projects(_READER) == [_READER]


class TestNameCollision:
    """One job name under two project ids must merge, not overwrite."""

    async def test_reliability_rollups_are_summed_not_replaced(self, store):
        # The reader has its own mirror carrying the SAME job name -- an
        # operator with a local source who is also granted the shared tenant.
        await _execution(
            store,
            project_id=_READER,
            execution_id="reader-own-1",
            job_name="Maximo2RHM_Workorders",
            started_at=_T0 + datetime.timedelta(hours=8),
            duration_ms=250,
        )
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        units, _, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
        )

        # 1 run in the reader's own mirror + 1 in the granted tenant. The dict
        # this replaced would have reported whichever query row came last.
        assert len(units) == 1
        assert units[0].total_runs == 2

    async def test_duration_percentiles_span_both_mirrors(self, store):
        await _execution(
            store,
            project_id=_READER,
            execution_id="reader-own-1",
            job_name="Maximo2RHM_Workorders",
            started_at=_T0 + datetime.timedelta(hours=8),
            duration_ms=900,
        )
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        units, _, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
        )

        # Grouping by (project, job) would have returned two rows for one key
        # and kept one of the two maxima.
        assert units[0].max_duration_ms == 900


class TestInsightsScoping:
    """`runtime_incidents` and `runtime_log_events` carry no `source_key`.

    Their scope arrives ONLY through the join to the execution population, so
    these tests are what stand between a shared read and a silent leak.
    """

    async def test_a_strangers_incident_never_reaches_the_reader(self, store, db_session):
        db_session.add_all(
            [
                RuntimeLogEventRow(
                    project_id=_STRANGER,
                    execution_id="prod-0",
                    timestamp=_T0,
                    severity="error",
                    message="stranger log line",
                    line_ordinal=0,
                    event_fingerprint="fp-stranger",
                    source_key=_SRC,
                    ingested_at=_T0,
                ),
                RuntimeIncidentRow(
                    project_id=_STRANGER,
                    incident_id="inc-stranger",
                    execution_id="prod-0",
                    signature_hash="sig-stranger",
                    pattern_id="sqlserver.deadlock",
                    confidence="high",
                    first_bad_at=_T0,
                    created_at=_T0,
                ),
            ]
        )
        await db_session.flush()
        # The owner's own failed execution, so there IS something to attach to.
        await _execution(
            store,
            project_id=_OWNER,
            execution_id="prod-fail",
            job_name="Maximo2RHM_Workorders",
            state="failed",
            started_at=_T0 + datetime.timedelta(hours=4),
        )
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        units, _, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
        )

        # The stranger's rows share an execution_id with the owner's mirror but
        # belong to a project outside the read set: the join must drop them.
        assert units[0].incident_insights == []
        assert units[0].log_quality.diagnosed_executions == 0
        assert units[0].log_quality.executions_with_logs == 0

    async def test_a_granted_tenants_failure_text_is_still_redacted(self, store):
        await _execution(
            store,
            project_id=_OWNER,
            execution_id="prod-fail",
            job_name="Maximo2RHM_Workorders",
            state="failed",
            started_at=_T0 + datetime.timedelta(hours=4),
        )
        await store.upsert_failure(
            _OWNER,
            RuntimeFailure(
                execution_id="prod-fail",
                job_name="Maximo2RHM_Workorders",
                failed_at=_T0 + datetime.timedelta(hours=4),
                error_summary="Connect failed for user admin password=hunter2",
            ),
            _SRC,
            _T0,
        )
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects = await store.resolve_runtime_read_projects(_READER)

        units, _, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
        )

        summary = units[0].recent_failures[0].error_summary
        assert "hunter2" not in summary


class TestScopeDeclaration:
    """A run that reads a mirror it does not own has to say so."""

    async def test_an_owned_read_is_not_marked_shared(self, store):
        await _source(store)

        scope = await store.resolve_doc_read_scope(
            [_OWNER], _OWNER, default_window_days=90
        )

        assert scope.shared_read is False
        assert scope.shared_from == []
        assert scope.read_project_ids == [_OWNER]

    async def test_a_granted_read_names_its_source(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        scope = await store.resolve_doc_read_scope(
            await store.resolve_runtime_read_projects(_READER),
            _READER,
            default_window_days=90,
        )

        assert scope.shared_read is True
        assert scope.shared_from == ["Talend EU"]
        assert scope.read_project_ids == [_READER, _OWNER]

    async def test_the_grants_window_overrides_the_callers_default(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=30,
            created_by="1",
        )

        scope = await store.resolve_doc_read_scope(
            await store.resolve_runtime_read_projects(_READER),
            _READER,
            default_window_days=90,
        )

        assert scope.window_days == 30
        assert scope.configured is True
