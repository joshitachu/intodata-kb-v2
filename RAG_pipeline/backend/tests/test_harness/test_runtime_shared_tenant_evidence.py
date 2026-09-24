"""The agent's runtime tools, read through a shared vendor tenant.

`runtime_sources.physical_identity` is globally unique on purpose: registering
one TMC or Elasticsearch tenant twice means a second vendor poll and two mirrors
of one estate free to drift apart. So a second project reaches that tenant
through a READ GRANT over the existing mirror.

Until this change the grant reached the documentation lane and nothing else.
`query_structured_runtime_evidence` and `search_runtime_log_evidence` intersected
the caller's scope down to the named `project_id` and used it as a raw row-stamp
filter, so a granted reader project could publish a document about the estate and
then be told, in the same session, that there was no runtime data when it asked a
question about it. `tests/test_runtime/test_read_scope.py` pins the repository
predicates; this file pins the wiring above them, where the two halves of a
resolved read can be separated.

The one property every test here circles
----------------------------------------
`RuntimeReadScope.scope` is WIDENED to include the granting project. The grant's
environment and window limits are NOT in it -- they live in `.terms`. A widened
scope passed on without its terms therefore returns the grantor's rows with the
grant's own narrowing silently unapplied: every environment, the whole history.
That is a data leak, and it looks exactly like a feature working.

`test_a_narrowed_grant_...` is the behavioural guard for it (it fails by
RETURNING TOO MUCH), and `test_every_operation_pairs_...` is the structural one
(it fails the moment an operation widens the scope without stamping the terms).
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest

from src.db.models import runtime_data  # noqa: F401 - registers the source tables
from src.harness.evidence import (
    _RUNTIME_EVIDENCE_OPERATIONS,
    _with_read_terms,
    query_structured_runtime_evidence,
    search_runtime_log_evidence,
)
from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.read_scope import RuntimeReadScope
from src.runtime.store import RuntimeStoreRepository

# OWNER holds the credential and pays the vendor poll. READER questions the same
# tenant through a grant AND has a small mirror of its own -- that second mirror
# is what makes a flat environment filter visibly wrong, because a grant narrowed
# to the owner's PROD must not touch the reader's own ACC rows.
_OWNER = "proj-owner"
_READER = "proj-reader"
_STRANGER = "proj-stranger"
_SRC = "tmc"
_LABEL = "Talend EU"
_PROD = "env-prod"
_ACC = "env-acc"
_T0 = datetime.datetime(2026, 8, 1, 6, 0, tzinfo=UTC)


async def _mirror_run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    run_id: str,
    job_name: str,
    environment_id: str,
    message: str,
    started_at: datetime.datetime = _T0,
) -> None:
    """One run exactly as the sync writer records it: run + execution + log line.

    All three carry the same id because the TMC provider derives the execution
    FROM the job run. That identity is what lets a grant narrowed to an
    environment reach `runtime_executions` and `runtime_log_events`, neither of
    which has an environment column of its own.
    """

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


@pytest.fixture
async def store(db_session) -> RuntimeStoreRepository:
    """OWNER's tenant (a PROD job and an ACC job) plus READER's own ACC mirror.

    Every log line contains the word "timeout" so one search reaches all of them
    and the assertions are about WHICH rows came back, never about matching.
    """

    repo = RuntimeStoreRepository(db_session)
    await _mirror_run(
        repo,
        project_id=_OWNER,
        run_id="owner-prod-1",
        job_name="Owner_Prod_Job",
        environment_id=_PROD,
        message="owner prod timeout",
    )
    await _mirror_run(
        repo,
        project_id=_OWNER,
        run_id="owner-acc-1",
        job_name="Owner_Acc_Job",
        environment_id=_ACC,
        message="owner acc timeout",
    )
    await _mirror_run(
        repo,
        project_id=_READER,
        run_id="reader-acc-1",
        job_name="Reader_Acc_Job",
        environment_id=_ACC,
        message="reader acc timeout",
    )
    await _mirror_run(
        repo,
        project_id=_STRANGER,
        run_id="stranger-1",
        job_name="Stranger_Job",
        environment_id=_PROD,
        message="stranger timeout",
    )
    return repo


async def _grant(
    store: RuntimeStoreRepository,
    *,
    environment_ids: list[str] | None = None,
    window_days: int | None = None,
    to_project: str = _READER,
) -> None:
    """One operator-created grant over OWNER's registered source."""

    source = await store.create_source_config(
        project_id=_OWNER, source_type="mock", label=_LABEL
    )
    await store.add_read_grant(
        source_id=source.id,
        project_id=to_project,
        environment_ids=environment_ids,
        window_days=window_days,
        created_by="1",
    )


def _execution_refs(result: Any) -> set[str]:
    return {
        str(item.metadata.get("execution_ref"))
        for item in result.items
        if item.metadata.get("execution_ref")
    }


def _titles(result: Any) -> set[str]:
    return {item.title for item in result.items}


class TestLogSearchReachesTheGrantedTenant:
    """`search_logs` is how the agent answers "what happened in this tenant".

    If the read set does not reach this call the feature does not exist for
    chat, whatever the other eleven operations do.
    """

    async def test_a_granted_reader_gets_the_grantors_log_lines(
        self, db_session, store
    ) -> None:
        """The headline case. Before this change the result was empty.

        READER holds no scope over OWNER and never names it. The widening comes
        entirely from the stored grant row, resolved server-side against the
        scope READER already has.
        """

        await _grant(store)

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=20,
        )

        assert result.status == "succeeded"
        assert _execution_refs(result) == {
            "owner-prod-1",
            "owner-acc-1",
            "reader-acc-1",
        }
        # ...and stops at the grant. A third project's tenant is not shared with
        # anyone and must not appear because a read happened to widen.
        assert "stranger-1" not in _execution_refs(result)

    async def test_the_result_tells_the_model_the_mirror_was_shared(
        self, db_session, store
    ) -> None:
        """A runtime job vocabulary belongs to the TENANT, not to the reader.

        An answer drawn from a shared mirror is not comparable to one drawn from
        an owned mirror -- job names below can belong to a sibling project -- so
        the agent has to be able to say which it was. Same two fields the
        documentation lane reports (`RuntimeDocReadScope.shared_read` /
        `.shared_from`), and source LABELS rather than the grantor's project id,
        which the reader may hold no permission to see.
        """

        await _grant(store)

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
        )

        assert result.scope is not None
        assert result.scope["shared_read"] is True
        assert result.scope["shared_from"] == [_LABEL]
        assert _OWNER not in str(result.scope)
        # The measured truncation incident in `evidence.py` is why this is
        # capped rather than explanatory: a bigger payload cost 21 tool calls.
        assert len(result.scope["shared_read_note"]) < 200

    async def test_a_narrowed_grant_returns_the_grantors_prod_rows_only(
        self, db_session, store
    ) -> None:
        """THE leak guard, and it fails by returning TOO MUCH.

        The grant names PROD, so it binds OWNER's rows to PROD. It must not
        touch READER's own ACC rows -- those are READER's, reached without any
        grant. Three assertions that no flat environment filter can satisfy at
        once: a union leaks `owner-acc-1`, an intersection hides
        `reader-acc-1`, and dropping the terms while keeping the widened scope
        leaks `owner-acc-1` too.
        """

        await _grant(store, environment_ids=[_PROD])

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=20,
        )

        refs = _execution_refs(result)
        assert "owner-prod-1" in refs
        assert "owner-acc-1" not in refs
        assert "reader-acc-1" in refs

    async def test_an_estate_wide_log_question_still_obeys_the_grants_environment(
        self, db_session, store
    ) -> None:
        """The exact shape in which dropping the terms LEAKS rather than hides.

        With a `project_id` on the params, a lost read set falls back to the
        single-project predicate and the reader merely sees too little. With no
        `project_id` -- "what happened overnight", the most natural way to ask --
        the only remaining filter is the RBAC scope, and that scope has just been
        widened to include the grantor. Every environment of the grantor's estate
        then comes back, redaction being the only thing left in the way.
        """

        await _grant(store, environment_ids=[_PROD])

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            limit=20,
        )

        refs = _execution_refs(result)
        assert refs == {"owner-prod-1", "reader-acc-1"}
        assert "owner-acc-1" not in refs

    async def test_a_windowed_grant_bounds_the_grantors_history_not_the_readers(
        self, db_session, store
    ) -> None:
        """A window is how much of the grantor's HISTORY a reader may see.

        The reader's own term is unrestricted by construction, so an old row of
        its own survives a window that removes an equally old row of the
        grantor's. Getting this backwards silently shrinks a project's own log
        console the day somebody grants it something.
        """

        ancient = _T0 - datetime.timedelta(days=200)
        await _mirror_run(
            store,
            project_id=_OWNER,
            run_id="owner-prod-ancient",
            job_name="Owner_Prod_Job",
            environment_id=_PROD,
            message="ancient owner timeout",
            started_at=ancient,
        )
        await _mirror_run(
            store,
            project_id=_READER,
            run_id="reader-acc-ancient",
            job_name="Reader_Acc_Job",
            environment_id=_ACC,
            message="ancient reader timeout",
            started_at=ancient,
        )
        await _grant(store, window_days=30)

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=20,
        )

        refs = _execution_refs(result)
        assert "owner-prod-ancient" not in refs
        assert "reader-acc-ancient" in refs

    async def test_a_project_named_only_by_the_model_widens_nothing(
        self, db_session, store
    ) -> None:
        """`project_id` arrives from an LLM tool argument. It may only NARROW.

        OWNER has granted READER, so the grant row that would widen this read
        exists -- it is simply attached to a project this caller cannot see. A
        tool argument naming OWNER must therefore resolve to an empty read, not
        to OWNER's rows via OWNER's own grants. This is the difference between
        "the model chose the scope" and "the model chose within the scope".
        """

        await _grant(store)

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_OWNER,
            limit=20,
        )

        assert result.status == "succeeded"
        assert result.items == []
        assert result.scope is None

    async def test_an_unrelated_project_in_the_selection_is_dropped_not_read(
        self, db_session, store
    ) -> None:
        """Same rule through the other model-authored argument, `project_ids`."""

        await _grant(store)

        result = await search_runtime_log_evidence(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_ids=[_READER, _STRANGER],
            limit=20,
        )

        assert "stranger-1" not in _execution_refs(result)
        # The grant READER does hold still applies: narrowing by an unauthorized
        # id must not also suppress the legitimate widening.
        assert "owner-prod-1" in _execution_refs(result)


class TestStructuredEvidenceReachesTheGrantedTenant:
    """The other entry point, over the same mirror."""

    async def test_a_granted_reader_sees_the_grantors_executions(
        self, db_session, store
    ) -> None:
        await _grant(store)

        result = await query_structured_runtime_evidence(
            operation="list_executions",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=50,
        )

        assert result.status == "succeeded"
        assert _titles(result) == {
            "Owner_Prod_Job",
            "Owner_Acc_Job",
            "Reader_Acc_Job",
        }
        assert result.scope["shared_read"] is True
        assert result.scope["shared_from"] == [_LABEL]

    async def test_a_narrowed_grant_narrows_the_grantors_executions_only(
        self, db_session, store
    ) -> None:
        """`runtime_executions` carries no environment column, so this narrowing
        has to route through the execution ids observed in that environment. If
        that path is not wired the result is OWNER's complete run history."""

        await _grant(store, environment_ids=[_PROD])

        result = await query_structured_runtime_evidence(
            operation="list_executions",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=50,
        )

        assert _titles(result) == {"Owner_Prod_Job", "Reader_Acc_Job"}

    async def test_a_narrowed_grant_narrows_the_inventory_read_too(
        self, db_session, store
    ) -> None:
        """`list_runtime_jobs` is inventory, and inventory rows DO carry an
        environment. The narrowing is therefore direct rather than via run ids,
        and a wiring that only happened to work for run-shaped tables would pass
        the test above and fail this one."""

        await _grant(store, environment_ids=[_PROD])

        result = await query_structured_runtime_evidence(
            operation="list_runtime_jobs",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=50,
        )

        assert _titles(result) == {"Owner_Prod_Job", "Reader_Acc_Job"}

    async def test_a_project_named_only_by_the_model_widens_nothing(
        self, db_session, store
    ) -> None:
        await _grant(store)

        result = await query_structured_runtime_evidence(
            operation="list_executions",
            project_scope=[_READER],
            db=db_session,
            project_id=_OWNER,
            limit=50,
        )

        assert result.items == []
        assert "shared_read" not in result.scope

    async def test_an_estate_wide_question_is_widened_too(
        self, db_session, store
    ) -> None:
        """Naming no project used to mean no widening at all.

        `resolve_runtime_read_scope` has nothing to widen from without a project
        name, so an estate-wide question would have gone on filtering by the raw
        row stamp -- the pre-grant behaviour, in the one call shape an operator
        is most likely to use first. `resolve_runtime_read_scope_for_user` is
        why it does not.
        """

        await _grant(store)

        result = await query_structured_runtime_evidence(
            operation="list_executions",
            project_scope=[_READER],
            db=db_session,
            limit=50,
        )

        assert "Owner_Prod_Job" in _titles(result)
        assert result.scope["shared_read"] is True


class TestTheUngrantedPathIsUnchanged:
    """No grant must mean no change -- in the rows AND in the serialized result.

    Both tests compare the real resolver against the pre-change behaviour,
    reconstructed as `RuntimeReadScope.unscoped([project])`: the same scope the
    old intersection produced, with no terms and no disclosure. Byte equality of
    the whole model is the assertion, because the ways this could drift are the
    ones nobody would think to assert individually.
    """

    @staticmethod
    def _pin_legacy_read(monkeypatch: pytest.MonkeyPatch, scope: list[str]) -> None:
        async def legacy(_db, *, project_scope, project_id):  # noqa: ANN001
            return RuntimeReadScope.unscoped(scope)

        monkeypatch.setattr("src.harness.evidence._resolve_agent_runtime_read", legacy)

    async def test_log_search_is_byte_identical_without_a_grant(
        self, monkeypatch: pytest.MonkeyPatch, db_session, store
    ) -> None:
        kwargs: dict[str, Any] = dict(
            question="timeout",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=20,
        )

        resolved = await search_runtime_log_evidence(**kwargs)
        self._pin_legacy_read(monkeypatch, [_READER])
        legacy = await search_runtime_log_evidence(**kwargs)

        assert resolved.model_dump_json() == legacy.model_dump_json()
        assert _execution_refs(resolved) == {"reader-acc-1"}

    async def test_structured_evidence_is_byte_identical_without_a_grant(
        self, monkeypatch: pytest.MonkeyPatch, db_session, store
    ) -> None:
        kwargs: dict[str, Any] = dict(
            operation="list_executions",
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
            limit=50,
        )

        resolved = await query_structured_runtime_evidence(**kwargs)
        self._pin_legacy_read(monkeypatch, [_READER])
        legacy = await query_structured_runtime_evidence(**kwargs)

        assert resolved.model_dump_json() == legacy.model_dump_json()
        assert _titles(resolved) == {"Reader_Acc_Job"}


class TestScopeAndTermsAreNeverSeparated:
    """The structural half of the leak guard.

    The behavioural tests above catch a separation that reaches the database.
    These catch it at the boundary, for every operation, including the ones no
    behavioural test exercises -- which is where it would actually be introduced,
    by someone adding a thirteenth operation.
    """

    @pytest.mark.parametrize("operation", sorted(_RUNTIME_EVIDENCE_OPERATIONS))
    async def test_every_operation_pairs_a_widened_scope_with_its_terms(
        self, monkeypatch: pytest.MonkeyPatch, db_session, store, operation: str
    ) -> None:
        """Widened scope in the context, matching terms on the params. Both.

        Asserting only the scope is what would let the leak through: the grant's
        environment lives in the terms, so a context widened to [reader, owner]
        with `read_terms == []` reads every environment of the owner's estate
        and looks completely correct from the outside.
        """

        await _grant(store, environment_ids=[_PROD], window_days=30)
        seen: dict[str, Any] = {}
        method = _RUNTIME_EVIDENCE_OPERATIONS[operation].provider_method

        async def provider_call(_self: Any, params: Any, context: Any) -> Any:
            seen["params"] = params
            seen["scope"] = context.project_scope
            return []

        monkeypatch.setattr(
            f"src.harness.evidence.StoredRuntimeProvider.{method}", provider_call
        )

        await query_structured_runtime_evidence(
            operation=operation,
            project_scope=[_READER],
            db=db_session,
            project_id=_READER,
        )

        assert seen["scope"] == [_READER, _OWNER]
        terms = seen["params"].read_terms
        assert [term.project_id for term in terms] == [_READER, _OWNER]
        # The reader's own contribution carries no limit; the grantor's carries
        # exactly the two the operator set.
        assert terms[0].unrestricted
        assert terms[1].environment_ids == [_PROD]
        assert terms[1].since is not None

    async def test_an_input_that_cannot_carry_the_read_set_refuses_to_run(
        self,
    ) -> None:
        """A model without `read_terms` is a hard failure, never a silent drop.

        `AnalyzeExecutionFailureInput` is a real input model in this codebase
        that carries no read set (it is scoped by execution id). If an operation
        were ever pointed at one of these, the alternative to raising is to run
        it under the widened scope with the grant's limits dropped -- precisely
        the leak. It is a `ValueError` because the entry point already turns one
        into a failed read rather than an exception for the caller.
        """

        read = RuntimeReadScope(
            scope=[_READER, _OWNER],
            terms=[
                {"project_id": _READER},
                {"project_id": _OWNER, "environment_ids": [_PROD]},
            ],
        )

        with pytest.raises(ValueError, match="cannot carry a runtime read set"):
            _with_read_terms(AnalyzeExecutionFailureInput(execution_id="x"), read)

    async def test_no_terms_leaves_the_input_exactly_as_built(self) -> None:
        """An empty read set is "nothing was resolved", not "nothing visible".

        Stamping an empty list would still be correct for the repository, which
        treats empty as no-op -- but returning the same object makes the
        unshared path provably untouched rather than merely equivalent.
        """

        from src.runtime.models import ListExecutionsInput

        params = ListExecutionsInput(limit=7)
        assert _with_read_terms(params, RuntimeReadScope.unscoped(None)) is params
