"""Row-stamp filtering when a read spans a shared tenant.

`test_doc_evidence_shared_read.py` covers the DOCUMENTATION lane, which was the
only lane that ever applied a read grant. This file covers the other ~40 read
paths -- the operations console and the agent's runtime tools -- which filtered
on the raw row stamp `project_id = X` and therefore returned NOTHING to a
project reading through a grant.

The properties under test are the ones a grant is worth nothing without:

* the ungranted path is untouched; a single unrestricted term still emits the
  `==` predicate the lane had before read grants existed, not an `IN` and not a
  one-branch `OR`;
* a grant's environment and window bind the GRANTOR's rows and only those -- so
  a flat union (which would leak the grantor's other environments) and a flat
  intersection (which would hide the reader's own) both have to fail;
* the tables that cannot express an environment -- `runtime_artifacts` and
  friends -- return nothing rather than everything when a grant is narrowed;
* `search_log_events`, the read behind the agent's `search_logs` tool, narrows
  through `execution_id`, because `runtime_log_events` has no environment
  column and redaction is the only thing left if that predicate is wrong.
"""

import datetime
from datetime import UTC

import pytest
from sqlalchemy import select

from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.db.models.runtime_data import RuntimeJobRow
from src.runtime.models import (
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobsInput,
    RuntimeArtifact,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.read_scope import RuntimeReadScope, RuntimeReadTerm, resolve_runtime_read_scope
from src.runtime.store import RuntimeStoreRepository

# OWNER holds the credential and pays the poll. READER documents and questions
# the same tenant through a grant, and ALSO has a mirror of its own -- that
# second mirror is what makes a flat environment filter visibly wrong.
_OWNER = "proj-owner"
_READER = "proj-reader"
_SRC = "tmc"
_T0 = datetime.datetime(2026, 8, 1, 6, 0, tzinfo=UTC)
# Older than any grant window used here, and old enough that no default window
# could accidentally include it.
_ANCIENT = _T0 - datetime.timedelta(days=200)

_PROD = "env-prod"
_ACC = "env-acc"


async def _mirror_run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    run_id: str,
    job_name: str,
    environment_id: str,
    started_at: datetime.datetime,
    message: str,
) -> None:
    """One run as the sync writer records it: run + execution + log line.

    All three carry the same id, because the TMC provider derives the execution
    FROM the job run and copies `run_id` into `execution_id`. That identity is
    exactly what `_environment_execution_ids` relies on to give an environment
    to a table that has no environment column.
    """
    await store.upsert_job_run(
        project_id,
        RuntimeJobRun(
            run_id=run_id,
            job_id=f"job-{job_name}",
            job_name=job_name,
            source_key=_SRC,
            state=RuntimeExecutionState.SUCCESS,
            environment_id=environment_id,
            environment_name=environment_id.upper(),
            started_at=started_at,
            execution_id=run_id,
        ),
        _SRC,
        _T0,
    )
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=run_id,
            job_name=job_name,
            state=RuntimeExecutionState.SUCCESS,
            started_at=started_at,
            ended_at=started_at + datetime.timedelta(seconds=1),
            duration_ms=1000,
        ),
        _SRC,
        _T0,
    )
    await store.upsert_log_event(
        project_id,
        RuntimeLogEvent(
            execution_id=run_id,
            timestamp=started_at,
            severity=RuntimeLogSeverity.ERROR,
            message=message,
            line_ordinal=0,
        ),
        _SRC,
        _T0,
    )


async def _mirror_job(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    job_name: str,
    environment_id: str,
) -> None:
    """One inventory row: the job EXISTS, independent of any run of it."""
    await store.upsert_job(
        project_id,
        RuntimeJob(
            job_id=f"job-{job_name}",
            source_key=_SRC,
            name=job_name,
            environment_id=environment_id,
            environment_name=environment_id.upper(),
        ),
        _SRC,
        _T0,
    )


@pytest.fixture
async def store(db_session):
    """One tenant mirrored under OWNER, plus READER's own small mirror.

    OWNER has a PROD job and an ACC job; READER's own job is ACC. A grant
    narrowed to PROD must therefore return OWNER's prod rows, none of OWNER's
    acc rows, and ALL of READER's acc rows -- three assertions one flat
    environment filter cannot satisfy at once.
    """
    repo = RuntimeStoreRepository(db_session)

    await _mirror_job(repo, project_id=_OWNER, job_name="Owner_Prod_Job", environment_id=_PROD)
    await _mirror_job(repo, project_id=_OWNER, job_name="Owner_Acc_Job", environment_id=_ACC)
    await _mirror_job(repo, project_id=_READER, job_name="Reader_Acc_Job", environment_id=_ACC)

    await _mirror_run(
        repo,
        project_id=_OWNER,
        run_id="owner-prod-1",
        job_name="Owner_Prod_Job",
        environment_id=_PROD,
        started_at=_T0,
        message="owner prod line",
    )
    await _mirror_run(
        repo,
        project_id=_OWNER,
        run_id="owner-acc-1",
        job_name="Owner_Acc_Job",
        environment_id=_ACC,
        started_at=_T0,
        message="owner acc line",
    )
    await _mirror_run(
        repo,
        project_id=_OWNER,
        run_id="owner-prod-ancient",
        job_name="Owner_Prod_Job",
        environment_id=_PROD,
        started_at=_ANCIENT,
        message="owner prod line from long ago",
    )
    await _mirror_run(
        repo,
        project_id=_READER,
        run_id="reader-acc-1",
        job_name="Reader_Acc_Job",
        environment_id=_ACC,
        started_at=_T0,
        message="reader acc line",
    )

    for project_id, artifact_id in ((_OWNER, "owner-artifact"), (_READER, "reader-artifact")):
        await repo.upsert_artifact(
            project_id,
            RuntimeArtifact(artifact_id=artifact_id, source_key=_SRC, name=artifact_id),
            _SRC,
            _T0,
        )
    return repo


async def _grant(
    store: RuntimeStoreRepository,
    *,
    environment_ids: list[str] | None,
    window_days: int | None = None,
) -> None:
    source = await store.create_source_config(
        project_id=_OWNER, source_type="mock", label="Talend EU"
    )
    await store.add_read_grant(
        source_id=source.id,
        project_id=_READER,
        environment_ids=environment_ids,
        window_days=window_days,
        created_by="1",
    )


async def _reader_scope(store: RuntimeStoreRepository) -> RuntimeReadScope:
    """The read set as a route resolves it, with a fixed `now`.

    The window floor is computed once at resolution time, so pinning `now` here
    is what keeps a page and its count comparing against the same instant.
    """
    return await resolve_runtime_read_scope(
        store, user_scope=None, project_id=_READER, now=_T0
    )


class TestRegressionFloor:
    """No grant means no change -- in the results AND in the emitted SQL."""

    async def test_an_ungranted_read_emits_a_bare_equality(self, store):
        """The predicate itself, with no RBAC scope alongside it to read past.

        `_apply_scope` contributes its own `IN` on the same column, so asserting
        on a whole statement would not distinguish "the read set collapsed to an
        equality" from "the scope filter happens to be there".
        """
        scope = await _reader_scope(store)
        assert len(scope.terms) == 1 and scope.terms[0].unrestricted

        stmt = store._apply_project_filter(
            select(RuntimeJobRow),
            RuntimeJobRow.project_id,
            ListRuntimeJobsInput(project_id=_READER, read_terms=scope.terms),
            environment_column=RuntimeJobRow.environment_id,
        )

        sql = str(stmt.compile())
        assert "runtime_jobs.project_id = " in sql
        # Not an IN, not a one-branch OR: the unshared path keeps the plan and
        # the index use it had before read grants existed.
        assert " OR " not in sql
        assert "project_id IN" not in sql

    async def test_no_terms_and_no_project_leaves_the_statement_alone(self, store):
        """"Nothing was resolved" is not "nothing is visible"."""
        params = ListRuntimeJobsInput()
        stmt = select(RuntimeJobRow)

        assert (
            store._apply_project_filter(stmt, RuntimeJobRow.project_id, params) is stmt
        )

    async def test_results_match_the_pre_change_predicate_exactly(self, store):
        scope = await _reader_scope(store)

        widened = await store.list_jobs(
            scope.scope,
            ListRuntimeJobsInput(project_id=_READER, read_terms=scope.terms),
        )
        # Exactly what the call looked like before this change existed.
        unwidened = await store.list_jobs(None, ListRuntimeJobsInput(project_id=_READER))

        assert [job.name for job in widened] == [job.name for job in unwidened]
        assert [job.name for job in widened] == ["Reader_Acc_Job"]

    async def test_a_read_that_names_no_project_is_not_widened(self, store):
        """`project_id=None` resolves to no terms at all, not to "everything"."""
        await _grant(store, environment_ids=[_PROD])

        scope = await resolve_runtime_read_scope(
            store, user_scope=["proj-a", "proj-b"], project_id=None, now=_T0
        )

        assert scope.terms == []
        assert scope.project_ids is None
        assert scope.scope == ["proj-a", "proj-b"]
        assert scope.shared_read is False


class TestGrantedRead:
    async def test_the_reader_sees_its_own_rows_and_the_grantors(self, store):
        await _grant(store, environment_ids=None)
        scope = await _reader_scope(store)

        jobs = await store.list_jobs(
            scope.scope, ListRuntimeJobsInput(project_id=_READER, read_terms=scope.terms)
        )

        assert sorted(job.name for job in jobs) == [
            "Owner_Acc_Job",
            "Owner_Prod_Job",
            "Reader_Acc_Job",
        ]

    async def test_a_page_and_its_count_agree(self, store):
        """Different predicates on the two sides means a total nobody can page to."""
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)
        params = ListRuntimeJobRunsInput(project_id=_READER, read_terms=scope.terms)

        runs = await store.list_job_runs(scope.scope, params)
        total = await store.count_job_runs(scope.scope, params)

        assert total == len(runs)


class TestEnvironmentNarrowing:
    """A grant's environment is a boundary on the GRANTOR's estate only."""

    async def test_prod_grant_keeps_the_grantors_prod_and_the_readers_acc(self, store):
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)

        runs = await store.list_job_runs(
            scope.scope,
            ListRuntimeJobRunsInput(project_id=_READER, read_terms=scope.terms, limit=100),
        )

        names = {run.run_id for run in runs}
        # The grantor's PROD run, and the reader's OWN acc run in full.
        assert "owner-prod-1" in names
        assert "reader-acc-1" in names
        # A flat union of the environment selections would have kept this one.
        assert "owner-acc-1" not in names
        # A flat intersection would have dropped `reader-acc-1` above; assert it
        # explicitly so the test cannot pass by accident if it starts returning
        # nothing at all.
        assert len(names) == 3  # + the grantor's ancient prod run

    async def test_narrowing_reaches_executions_through_their_job_runs(self, store):
        """`runtime_executions` has no environment column; the job-run table is
        the environment authority, joined on the execution id."""
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)

        executions = await store.list_executions(
            scope.scope, ListExecutionsInput(read_terms=scope.terms, limit=100)
        )

        ids = {execution.execution_id for execution in executions}
        assert "owner-prod-1" in ids
        assert "reader-acc-1" in ids
        assert "owner-acc-1" not in ids

    async def test_narrowing_reaches_the_log_search_the_agent_uses(self, store):
        """The read behind `search_logs`. If this predicate is wrong the grantor's
        other environments come back verbatim, redaction being all that is left."""
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)

        hits = await store.search_log_events(
            scope.scope, query="line", read_terms=scope.terms, limit=50
        )

        snippets = {hit["snippet"] for hit in hits}
        assert "owner prod line" in snippets
        assert "reader acc line" in snippets
        assert "owner acc line" not in snippets

    async def test_an_empty_environment_list_exposes_nothing_of_the_grantor(self, store):
        """`[]` is a deliberate "nothing" and must never be read as "unset"."""
        await _grant(store, environment_ids=[])
        scope = await _reader_scope(store)

        jobs = await store.list_jobs(
            scope.scope, ListRuntimeJobsInput(project_id=_READER, read_terms=scope.terms)
        )

        assert [job.name for job in jobs] == ["Reader_Acc_Job"]

    async def test_a_narrowed_grant_exposes_no_artifacts_at_all(self, store):
        """`runtime_artifacts` has neither an environment column nor an execution
        id, so a narrowed grant fails CLOSED there. Returning the grantor's whole
        artifact inventory instead would be the worse failure."""
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)
        params = ListRuntimeArtifactsInput(project_id=_READER, read_terms=scope.terms)

        artifacts = await store.list_artifacts(scope.scope, params)
        total = await store.count_artifacts(scope.scope, params)

        assert [artifact.artifact_id for artifact in artifacts] == ["reader-artifact"]
        assert total == 1

    async def test_an_unnarrowed_grant_does_expose_artifacts(self, store):
        """The fail-closed rule must be a consequence of NARROWING, not a blanket
        refusal to share the inventory tables."""
        await _grant(store, environment_ids=None)
        scope = await _reader_scope(store)

        artifacts = await store.list_artifacts(
            scope.scope, ListRuntimeArtifactsInput(project_id=_READER, read_terms=scope.terms)
        )

        assert sorted(artifact.artifact_id for artifact in artifacts) == [
            "owner-artifact",
            "reader-artifact",
        ]


class TestAggregateLane:
    """The rollups compose over `_scoped_execution_base`, not over a list."""

    async def test_reliability_spans_the_grant_within_its_environment(self, store):
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)

        rollups = await store.list_job_reliability(
            scope.scope,
            ListRuntimeJobReliabilityInput(project_id=_READER, read_terms=scope.terms),
        )

        names = {rollup.job_name for rollup in rollups}
        assert names == {"Owner_Prod_Job", "Reader_Acc_Job"}

    async def test_the_documentation_read_set_keeps_its_projects_and_gains_their_limits(
        self, store
    ):
        """`project_ids` and `read_terms` COMPOSE; the terms are not a second,
        rival read set.

        This test asserted the opposite until the documentation lane was found to
        leak through it. `project_ids` is the documentation lane's own flat
        resolution and names the granting project -- but it carries nothing of
        what that grant ALLOWS, so treating it as the winner returned the
        grantor's whole estate to a reader granted one environment of it. The
        two name the same projects (both walk the same grants in the same
        order), so preferring the terms loses no project and adds each grant's
        own boundary back.
        """
        await _grant(store, environment_ids=[_PROD])
        scope = await _reader_scope(store)

        rollups = await store.list_job_reliability(
            None,
            ListRuntimeJobReliabilityInput(
                project_id=_READER,
                project_ids=[_READER, _OWNER],
                read_terms=scope.terms,
            ),
        )

        assert {rollup.job_name for rollup in rollups} == {
            # The grant is still worth something: the owner's PROD job is read...
            "Owner_Prod_Job",
            # ...and the reader's own ACC job is not suppressed by the grant's
            # environment, which a flat intersection would have done.
            "Reader_Acc_Job",
        }


class TestWindowNarrowing:
    """A window bounds how much HISTORY a reader may see, not what EXISTS."""

    async def test_a_window_bounds_event_rows(self, store):
        await _grant(store, environment_ids=None, window_days=30)
        scope = await _reader_scope(store)

        runs = await store.list_job_runs(
            scope.scope,
            ListRuntimeJobRunsInput(project_id=_READER, read_terms=scope.terms, limit=100),
        )

        names = {run.run_id for run in runs}
        assert "owner-prod-1" in names
        assert "owner-prod-ancient" not in names
        # The reader's own term carries no window at all.
        assert "reader-acc-1" in names

    async def test_a_window_does_not_hide_inventory(self, store):
        """`runtime_jobs` says the job exists. A window grant that deleted jobs
        from the estate would report a live integration as absent."""
        await _grant(store, environment_ids=None, window_days=30)
        scope = await _reader_scope(store)

        jobs = await store.list_jobs(
            scope.scope, ListRuntimeJobsInput(project_id=_READER, read_terms=scope.terms)
        )

        assert sorted(job.name for job in jobs) == [
            "Owner_Acc_Job",
            "Owner_Prod_Job",
            "Reader_Acc_Job",
        ]


class TestResolution:
    """What the resolver hands the repository, before any SQL exists."""

    async def test_the_readers_own_term_is_never_narrowed(self, store):
        await _grant(store, environment_ids=[_PROD], window_days=30)

        scope = await _reader_scope(store)

        assert scope.terms[0] == RuntimeReadTerm(project_id=_READER)
        assert scope.terms[0].unrestricted is True
        assert scope.terms[1].project_id == _OWNER
        assert scope.terms[1].environment_ids == [_PROD]
        assert scope.terms[1].since == _T0 - datetime.timedelta(days=30)

    async def test_a_project_outside_the_callers_scope_is_denied_before_grants(self, store):
        await _grant(store, environment_ids=None)

        scope = await resolve_runtime_read_scope(
            store, user_scope=["proj-other"], project_id=_READER, now=_T0
        )

        assert scope.denies_all is True
        assert scope.terms == []
