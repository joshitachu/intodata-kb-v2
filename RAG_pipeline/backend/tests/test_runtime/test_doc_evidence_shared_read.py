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

import contextlib
import datetime
from datetime import UTC
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from src.config import settings
from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.db.models.runtime_data import RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanStep,
)
from src.runtime.read_scope import RuntimeReadTerm, resolve_runtime_read_scope
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
    started_at: datetime.datetime | None = None,
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
            started_at=started_at or _T0,
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


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    """A per-run Fernet key: a project's own documentation read scope lives in
    its source's credential blob, so reading or writing one costs a decrypt."""
    monkeypatch.setattr(
        settings, "source_credentials_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")


async def _source(store: RuntimeStoreRepository, project_id: str = _OWNER):
    return await store.create_source_config(
        project_id=project_id, source_type="mock", label="Talend EU"
    )


async def _reader_terms(store: RuntimeStoreRepository, project_id: str = _READER):
    """The per-project terms a documentation run resolves for this project.

    `user_scope=None` is what the machine-key documentation path passes: there is
    no user behind it, so there is no scope to check the project against, and
    widening still comes only from stored grant rows.
    """
    resolved = await resolve_runtime_read_scope(
        store, user_scope=None, project_id=project_id, now=_T0
    )
    return resolved.project_ids or [project_id], resolved.terms


@contextlib.contextmanager
def _recorded_sql(session):
    """Every statement the session compiles, as SQL text plus its bound values.

    Text alone is not enough: the two shapes under test differ by which VALUE
    lands in a `project_id = ?` predicate as much as by the predicate itself.
    """
    statements: list[tuple[str, dict]] = []
    original = session.execute

    async def recording(statement, *args, **kwargs):
        compiled = statement.compile(dialect=session.bind.dialect)
        statements.append((str(compiled), dict(compiled.params)))
        return await original(statement, *args, **kwargs)

    with patch.object(session, "execute", recording):
        yield statements


class TestRegressionFloor:
    """A project with no grants must behave exactly as it did before."""

    async def test_read_set_of_an_ungranted_project_is_itself_alone(self, store):
        assert await store.resolve_runtime_read_projects(_OWNER) == [_OWNER]

    async def test_an_ungranted_read_emits_byte_identical_sql_with_or_without_terms(
        self, store, db_session
    ):
        """The regression floor, checked on the SQL rather than on the rows.

        An ungranted project is the overwhelmingly common case and it must keep
        its query plan, not merely its results -- `_apply_read_terms` special-
        cases a single unrestricted term into a bare `==` precisely so that a
        one-element read set never becomes an `IN` or a one-branch `OR`. A
        result-only assertion would pass while that guarantee quietly rotted.

        Both runs go through the whole lane, so every sub-query is covered by
        construction: a new one added without the special case shows up here as
        a diff rather than as a slow query six months later.
        """
        arguments = dict(
            project_id=_OWNER,
            unit_names=["Maximo2RHM_Workorders", "RHM2Smartflow_Workorders"],
            since=_SINCE,
        )

        with _recorded_sql(db_session) as flat_sql:
            flat = await store.list_doc_evidence(
                [_OWNER], project_ids=[_OWNER], **arguments
            )
        with _recorded_sql(db_session) as termed_sql:
            termed = await store.list_doc_evidence(
                [_OWNER],
                project_ids=[_OWNER],
                read_terms=[RuntimeReadTerm(project_id=_OWNER)],
                **arguments,
            )

        assert flat_sql, "the lane must have issued queries for this to mean anything"
        assert termed_sql == flat_sql
        assert termed == flat

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


class TestEveryAggregateIsBounded:
    """One unbounded sub-query is a leak whatever the others do.

    The response is assembled from eight reads and all of them reach the same
    prompt, so it is not enough for the run count to respect a grant while the
    failure text, the plan list or the diagnosis does not. Each assertion below
    is a different sub-query and a different way of expressing the narrowing:
    directly on an environment column, through the execution id of the run that
    produced the row, or through the execution population an insights table is
    joined to.
    """

    @pytest.fixture
    async def granted_prod_only(self, store, db_session):
        """The owner runs ONE job in both environments; the reader is granted PROD.

        The ACC side carries every kind of evidence the response can publish, so
        a sub-query that forgot its terms shows up as a specific wrong field
        rather than as a wrong total.
        """
        await _execution(
            store,
            project_id=_OWNER,
            execution_id="acc-mx-1",
            job_name="Maximo2RHM_Workorders",
            state="failed",
            started_at=_T0 + datetime.timedelta(hours=5),
            duration_ms=5000,
        )
        await _job_run(
            store,
            project_id=_OWNER,
            run_id="acc-mx-1",
            job_name="Maximo2RHM_Workorders",
            environment_id="env-acc",
            environment_name="ACC",
            started_at=_T0 + datetime.timedelta(hours=5),
        )
        # A SECOND acceptance run, so the excluded environment has a gap of its
        # own: one run produces no inter-run gap at all, and a cadence assertion
        # that cannot see a leaked series is not an assertion.
        await _execution(
            store,
            project_id=_OWNER,
            execution_id="acc-mx-2",
            job_name="Maximo2RHM_Workorders",
            started_at=_T0 + datetime.timedelta(hours=6),
        )
        await _job_run(
            store,
            project_id=_OWNER,
            run_id="acc-mx-2",
            job_name="Maximo2RHM_Workorders",
            environment_id="env-acc",
            environment_name="ACC",
            started_at=_T0 + datetime.timedelta(hours=6),
        )
        await store.upsert_failure(
            _OWNER,
            RuntimeFailure(
                execution_id="acc-mx-1",
                job_name="Maximo2RHM_Workorders",
                failed_at=_T0 + datetime.timedelta(hours=5),
                error_summary="ACC-ONLY stack trace from the acceptance loop",
            ),
            _SRC,
            _T0,
        )
        db_session.add_all(
            [
                RuntimeLogEventRow(
                    project_id=_OWNER,
                    execution_id="acc-mx-1",
                    timestamp=_T0,
                    severity="error",
                    message="acc log line",
                    line_ordinal=0,
                    event_fingerprint="fp-acc",
                    source_key=_SRC,
                    ingested_at=_T0,
                ),
                RuntimeIncidentRow(
                    project_id=_OWNER,
                    incident_id="inc-acc",
                    execution_id="acc-mx-1",
                    signature_hash="sig-acc",
                    pattern_id="sqlserver.deadlock",
                    confidence="high",
                    first_bad_at=_T0,
                    created_at=_T0,
                ),
            ]
        )
        await db_session.flush()
        for plan_id, label, environment_id in (
            ("plan-prod", "PROD Plan", "env-prod"),
            ("plan-acc", "ACC Plan", "env-acc"),
        ):
            await store.upsert_plan(
                _OWNER,
                RuntimePlan(
                    plan_id=plan_id,
                    source_key=_SRC,
                    name=label,
                    display_name=label,
                    environment_id=environment_id,
                    steps=[
                        RuntimePlanStep(
                            step_id=f"{plan_id}-1",
                            name="step",
                            job_name="Maximo2RHM_Workorders",
                        )
                    ],
                ),
                _SRC,
                _T0,
            )
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=None,
            created_by="1",
        )
        return await _reader_terms(store)

    async def test_no_aggregate_carries_the_excluded_environment(
        self, store, granted_prod_only
    ):
        read_projects, terms = granted_prod_only

        units, _, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Maximo2RHM_Workorders"],
            since=_SINCE,
            project_ids=read_projects,
            read_terms=terms,
        )
        unit = units[0]

        # runtime_executions, reached through the reliability rollup: no
        # environment column, so the narrowing routes through the execution id.
        assert unit.total_runs == 1
        assert unit.failed_runs == 0
        # ...and the duration percentiles, which read the same table directly.
        assert unit.max_duration_ms == 100
        # runtime_job_runs: its own environment column.
        assert unit.environments == ["PROD"]
        # runtime_plans: inventory with an environment column, so a narrowed
        # grant is honoured here rather than failing closed.
        assert unit.plans == ["PROD Plan"]
        # runtime_failures: no environment, narrowed through its execution id.
        # Getting this one wrong publishes an acceptance stack trace as the
        # production failure mode.
        assert unit.recent_failures == []
        # runtime_log_events: no project predicate of its own at all, reached
        # only through the (narrowed) execution population.
        assert unit.log_quality.failed_executions == 0
        assert unit.log_quality.executions_with_logs == 0
        # runtime_incidents: same join, same population.
        assert unit.incident_insights == []

    async def test_the_cadence_series_never_pools_an_excluded_environment(
        self, store, granted_prod_only
    ):
        """Asserted on the sub-query, not on the published field.

        `environments[0]` already selects which environment's gaps are
        published, so a leak here is invisible from the outside until the
        dominant environment changes. The dict itself must not contain the
        excluded one.
        """
        read_projects, terms = granted_prod_only

        gaps = await store._doc_evidence_gap_quantiles(
            read_projects,
            read_projects,
            ["Maximo2RHM_Workorders"],
            _SINCE,
            read_terms=terms,
        )

        assert all(environment != "ACC" for _job, environment in gaps)

    async def test_a_scan_map_override_is_not_honoured_outside_the_grant(
        self, store, db_session, granted_prod_only
    ):
        """The override resolves a runtime NAME off a job run.

        `runtime_job_scan_maps` carries no environment of its own, so the limits
        attach to the run it names: an override that can only be resolved
        through an excluded run must not rename a unit into the granted estate.
        """
        read_projects, terms = granted_prod_only
        db_session.add(
            RuntimeJobScanMapRow(
                project_id=_OWNER,
                source_key=_SRC,
                runtime_job_id="job-Acceptance_Only_Job",
                static_job_id="Static_Unit",
                created_at=_T0,
                created_by="1",
            )
        )
        await db_session.flush()

        _units, unmatched, _ = await store.list_doc_evidence(
            read_projects,
            project_id=_READER,
            unit_names=["Static_Unit"],
            since=_SINCE,
            project_ids=read_projects,
            read_terms=terms,
        )

        assert unmatched == ["Static_Unit"]


class TestCoverageRecordUnderANarrowedGrant:
    """The record has to describe what was READ, not what was asked for.

    The flat fields cannot: `environment_ids` unions the grant's environments
    into the reader's own selection and `window_days` takes the widest of the
    two, which is right for the reader's own sources and says nothing true about
    a granted project. A consumer reading only those fields would publish a
    bounded read as if it were whole.
    """

    async def test_a_narrowed_grant_is_named_with_its_limits(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=30,
            created_by="1",
        )
        read_projects, terms = await _reader_terms(store)

        scope = await store.resolve_doc_read_scope(
            read_projects, _READER, default_window_days=90, read_terms=terms
        )

        assert [limit.project_id for limit in scope.grant_limits] == [_OWNER]
        assert scope.grant_limits[0].environment_ids == ["env-prod"]
        assert scope.grant_limits[0].since == _T0 - datetime.timedelta(days=30)
        # The reader's own term restricts nothing and is not a limitation.
        assert all(not limit.unrestricted for limit in scope.grant_limits)

    async def test_an_unnarrowed_grant_declares_no_limit(self, store):
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )
        read_projects, terms = await _reader_terms(store)

        scope = await store.resolve_doc_read_scope(
            read_projects, _READER, default_window_days=90, read_terms=terms
        )

        assert scope.shared_read is True
        assert scope.grant_limits == []

    async def test_an_environment_the_grant_excludes_is_reported_unavailable(self, store):
        """The reader selects ACC for itself and is granted PROD only.

        ACC exists in the mirror -- in the grantor's estate -- but no query this
        read issues can return a row from it. Resolving it to a name off the
        un-narrowed mirror would publish it as an environment the document
        covers; reporting it as unavailable is the same treatment a retired
        environment already gets, and for the same reason.
        """
        reader_source = await _source(store, project_id=_READER)
        await store.write_credentials(
            source_id=reader_source.id,
            plaintext_payload={"doc_environment_ids": ["env-acc"]},
            credential_type="mock",
            rotated_by="1",
        )
        owner_source = await _source(store)
        await store.add_read_grant(
            source_id=owner_source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=None,
            created_by="1",
        )
        read_projects, terms = await _reader_terms(store)

        scope = await store.resolve_doc_read_scope(
            read_projects, _READER, default_window_days=90, read_terms=terms
        )

        # The flat selection still carries both -- the grant's environment is
        # what makes PROD readable at all, and the reader's own choice stands.
        assert scope.environment_ids == ["env-acc", "env-prod"]
        assert scope.environment_names == ["PROD"]
        assert scope.unavailable_environment_ids == ["env-acc"]

    async def test_the_picker_stops_offering_what_the_grant_excludes(self, store):
        """A selection that can only resolve to no evidence is not a choice."""
        source = await _source(store)
        await store.add_read_grant(
            source_id=source.id,
            project_id=_READER,
            environment_ids=["env-prod"],
            window_days=None,
            created_by="1",
        )
        read_projects, terms = await _reader_terms(store)

        offered = await store.list_mirrored_environments(
            read_projects, _READER, project_ids=read_projects, read_terms=terms
        )

        assert {item.environment_id for item in offered} == {"env-prod"}
