"""The aggregate half of `runtime.stored.query_evidence`.

`StoredRuntimeProvider` has implemented server-side aggregation the whole time;
the capability dispatched five of its methods, so every ranking, trend or
per-job summary question had to be assembled by paging `list_executions` -- 50
flat rows at a time against a turn that budgets a handful of evidence calls.
These tests cover the six aggregate operations that close that gap, and the
invariants that stop the capability schema and the dispatch table drifting apart
again.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.harness.evidence import (
    _RUNTIME_EVIDENCE_OPERATIONS,
    query_structured_runtime_evidence,
)
from src.harness.models import STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY
from src.runtime.models import (
    RuntimeErrorSignature,
    RuntimeEstateOverview,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobHealth,
    RuntimeJobReliability,
    RuntimeJobThroughput,
    RuntimeOutcomeTrendPoint,
    RuntimeTrendBucket,
    RuntimeUnhealthySummary,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.stored import StoredRuntimeProvider

_OPERATION_ENUM: list[str] = list(
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.input_schema["properties"]["operation"]["enum"]
)

#: The six operations this change adds, with one realistic provider return value
#: each. Keyed by operation so a new entry in the dispatch table without a test
#: is visible in the parametrization rather than silently uncovered.
_NEW_OPERATIONS: dict[str, Any] = {
    "list_runtime_jobs": [
        RuntimeJob(
            job_id="job-1",
            source_key="tmc-prod",
            name="DWH_Load_Customers",
            status="active",
            environment_id="env-prod",
        )
    ],
    "get_runtime_outcome_trend": [
        RuntimeOutcomeTrendPoint(
            bucket_start=datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC),
            total_runs=40,
            success_runs=36,
            failed_runs=4,
            running_runs=0,
            unknown_runs=0,
            failure_rate=0.1,
            p50_duration_ms=1200,
        )
    ],
    "list_runtime_job_reliability": [
        RuntimeJobReliability(
            project_id="pluripharm",
            source_key="tmc-prod",
            job_name="DWH_Load_Customers",
            total_runs=120,
            success_runs=110,
            failed_runs=10,
            unfinished_runs=0,
            failure_rate=0.083,
            current_failure_streak=2,
            p95_duration_ms=9000,
        )
    ],
    "list_runtime_job_throughput": [
        RuntimeJobThroughput(
            project_id="pluripharm",
            source_key="tmc-prod",
            job_name="DWH_Load_Customers",
            executions_with_metrics=12,
            component_count=4,
            rows_total=1_250_000,
            rows_rejected=17,
        )
    ],
    "list_runtime_error_signatures": [
        RuntimeErrorSignature(
            project_id="pluripharm",
            signature_hash="abc123",
            error_code="SQL-1205",
            exception_class="SQLServerException",
            component_name="tDBOutput_1",
            causal_message="Deadlock victim while writing DIM_CUSTOMER",
            occurrence_count=31,
        )
    ],
    "list_runtime_unhealthy": RuntimeUnhealthySummary(
        overview=RuntimeEstateOverview(
            project_id="pluripharm",
            sources_total=2,
            sources_failed=0,
            sources_partial=0,
            sources_demo=0,
            engines_total=3,
            engines_online=2,
            engines_degraded=1,
            engines_offline=0,
            engines_unknown=0,
            jobs_unhealthy=7,
            environments_total=2,
            workspaces_total=4,
        ),
        unhealthy_jobs=[
            RuntimeJobHealth(
                project_id="pluripharm",
                source_key="tmc-prod",
                job_id="job-1",
                job_name="DWH_Load_Customers",
                total_runs=120,
                failed_runs=10,
                failure_rate=0.083,
                current_failure_streak=2,
                latest_state=RuntimeExecutionState.FAILED,
            )
        ],
    ),
}


def _record_provider_call(
    monkeypatch: pytest.MonkeyPatch, operation: str, returns: Any
) -> dict[str, Any]:
    """Patch the one provider method `operation` must reach, and record its call."""

    seen: dict[str, Any] = {}
    method = _RUNTIME_EVIDENCE_OPERATIONS[operation].provider_method

    async def provider_call(_self: Any, params: Any, context: Any) -> Any:
        seen["method"] = method
        seen["params"] = params
        seen["scope"] = context.project_scope
        seen["db"] = context.db
        return returns

    monkeypatch.setattr(
        f"src.harness.evidence.StoredRuntimeProvider.{method}", provider_call
    )
    return seen


def test_every_schema_operation_has_a_dispatch_entry_and_a_real_provider_method() -> None:
    """The defect this change fixes was drift, so it is the drift that is asserted.

    `StoredRuntimeProvider` grew aggregation while the capability's enum and the
    dispatch branch were edited by hand and independently. An enum value with no
    table entry answers "not available" for a method that exists; a table entry
    naming a method that does not exist raises at call time.
    """

    assert set(_RUNTIME_EVIDENCE_OPERATIONS) == set(_OPERATION_ENUM)
    for operation, spec in _RUNTIME_EVIDENCE_OPERATIONS.items():
        assert hasattr(StoredRuntimeProvider, spec.provider_method), operation


def test_capability_schema_stays_closed_and_validates_the_new_arguments() -> None:
    """`additionalProperties: False` means an unlisted argument is a hard reject."""

    schema = STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.input_schema
    assert schema["additionalProperties"] is False
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    validator.validate(
        {
            "operation": "get_runtime_outcome_trend",
            "project_id": "pluripharm",
            "bucket": "hour",
            "since": "2026-09-01T00:00:00Z",
            "limit": 24,
        }
    )
    validator.validate(
        {
            "operation": "list_runtime_jobs",
            "environment_id": "env-prod",
            "job_status": "active",
        }
    )
    validator.validate(
        {"operation": "list_runtime_job_reliability", "attention_only": True}
    )
    validator.validate(
        {
            "operation": "list_runtime_error_signatures",
            "error_code": "SQL-1205",
            "exception_class": "SQLServerException",
            "component_name": "tDBOutput_1",
        }
    )


def test_aggregate_operations_are_named_as_the_cheap_path_in_the_guidance() -> None:
    """The description is how the model chooses. Nothing used to say that a
    ranking or trend had a one-call answer, so it reached for `list_executions`
    and drowned in flat rows."""

    capability = STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY
    use_when = " ".join(capability.use_when)
    do_not = " ".join(capability.do_not_use_when)

    for operation in _NEW_OPERATIONS:
        assert operation in use_when, operation
    assert "list_executions" in do_not
    # The question that started this: time-of-day pooling does not exist, and
    # the guidance has to say so rather than let hourly buckets stand in for it.
    assert "22:00" in do_not


@pytest.mark.parametrize("operation", sorted(_NEW_OPERATIONS))
async def test_new_operation_dispatches_to_its_provider_method_and_returns_items(
    monkeypatch: pytest.MonkeyPatch, operation: str, db_session) -> None:
    seen = _record_provider_call(monkeypatch, operation, _NEW_OPERATIONS[operation])
    db = db_session

    result = await query_structured_runtime_evidence(
        operation=operation,
        project_scope=["pluripharm"],
        db=db,
        project_id="pluripharm",
    )

    assert seen["method"] == _RUNTIME_EVIDENCE_OPERATIONS[operation].provider_method
    assert result.status == "succeeded"
    assert result.items, operation
    assert all(
        item.capability_id == STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id
        for item in result.items
    )
    # Not an empty `{}`: an aggregate whose measures are filtered out of the
    # prose states no aggregate at all.
    assert all(len(item.summary) > 2 for item in result.items)


@pytest.mark.parametrize("operation", sorted(_NEW_OPERATIONS))
async def test_new_operation_carries_project_scope_into_the_provider_context(
    monkeypatch: pytest.MonkeyPatch, operation: str, db_session) -> None:
    """Project scope is the tenant access boundary, not a convenience filter."""

    seen = _record_provider_call(monkeypatch, operation, _NEW_OPERATIONS[operation])
    db = db_session

    await query_structured_runtime_evidence(
        operation=operation,
        project_scope=["pluripharm", "other-customer"],
        db=db,
        project_id="pluripharm",
    )

    assert seen["scope"] == ["pluripharm"]
    assert seen["db"] is db


@pytest.mark.parametrize("operation", sorted(_NEW_OPERATIONS))
async def test_new_operation_refuses_a_project_outside_the_authorized_scope(
    monkeypatch: pytest.MonkeyPatch, operation: str, db_session) -> None:
    """An unauthorized project narrows to the empty scope, never to "all"."""

    seen = _record_provider_call(monkeypatch, operation, _NEW_OPERATIONS[operation])

    await query_structured_runtime_evidence(
        operation=operation,
        project_scope=["pluripharm"],
        db=db_session,
        project_id="other-customer",
    )

    assert seen["scope"] == []


async def test_unhealthy_summary_is_a_single_object_and_is_still_iterable_evidence(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """`list_runtime_unhealthy` returns one model where every other read returns
    a sequence. Iterating it directly would walk the model's fields."""

    summary = _NEW_OPERATIONS["list_runtime_unhealthy"]
    assert isinstance(summary, RuntimeUnhealthySummary)
    _record_provider_call(monkeypatch, "list_runtime_unhealthy", summary)

    result = await query_structured_runtime_evidence(
        operation="list_runtime_unhealthy",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
    )

    assert result.status == "succeeded"
    assert len(result.items) == 2
    # The overview comes first and carries the estate-wide totals, so a `limit`
    # that cut the entity list cannot be read as the whole estate.
    assert "jobs_unhealthy" in result.items[0].summary
    assert result.items[1].title == "DWH_Load_Customers"


async def test_trend_bucket_reaches_the_provider_and_defaults_to_day(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """Without a bucket argument the trend is stuck on its daily default, which
    makes "per hour" and "per week" unaskable through the capability."""

    seen = _record_provider_call(
        monkeypatch,
        "get_runtime_outcome_trend",
        _NEW_OPERATIONS["get_runtime_outcome_trend"],
    )

    await query_structured_runtime_evidence(
        operation="get_runtime_outcome_trend",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
        bucket="week",
        limit=12,
    )
    assert seen["params"].bucket is RuntimeTrendBucket.WEEK
    assert seen["params"].limit == 12

    await query_structured_runtime_evidence(
        operation="get_runtime_outcome_trend",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
    )
    assert seen["params"].bucket is RuntimeTrendBucket.DAY


async def test_aggregate_rows_reach_the_model_dated(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """A rollup row's only timestamp is `last_run_at` / `bucket_start`. Neither
    was read, so every aggregate item arrived undated."""

    _record_provider_call(
        monkeypatch,
        "get_runtime_outcome_trend",
        _NEW_OPERATIONS["get_runtime_outcome_trend"],
    )
    trend = await query_structured_runtime_evidence(
        operation="get_runtime_outcome_trend",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
    )
    assert trend.items[0].observed_at == "2026-09-01T00:00:00Z"

    _record_provider_call(
        monkeypatch,
        "list_runtime_job_reliability",
        [
            RuntimeJobReliability(
                project_id="pluripharm",
                source_key="tmc-prod",
                job_name="DWH_Load_Customers",
                total_runs=1,
                success_runs=0,
                failed_runs=1,
                unfinished_runs=0,
                failure_rate=1.0,
                current_failure_streak=1,
                last_run_at=datetime.datetime(2026, 9, 16, tzinfo=datetime.UTC),
            )
        ],
    )
    reliability = await query_structured_runtime_evidence(
        operation="list_runtime_job_reliability",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
    )
    assert reliability.items[0].observed_at == "2026-09-16T00:00:00Z"


async def test_reliability_filters_reach_the_provider_input(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    seen = _record_provider_call(
        monkeypatch,
        "list_runtime_job_reliability",
        _NEW_OPERATIONS["list_runtime_job_reliability"],
    )
    since = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)

    await query_structured_runtime_evidence(
        operation="list_runtime_job_reliability",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
        job_name="DWH_Load_Customers",
        since=since,
        attention_only=True,
        limit=25,
    )

    params = seen["params"]
    assert params.job_name == "DWH_Load_Customers"
    assert params.since == since
    assert params.attention_only is True
    assert params.limit == 25


async def test_error_signature_filters_reach_the_provider_without_a_source_key(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """`ListRuntimeErrorSignaturesInput` has no `source_key` on purpose: signature
    rows carry none, so accepting one would claim a filter that never applied."""

    seen = _record_provider_call(
        monkeypatch,
        "list_runtime_error_signatures",
        _NEW_OPERATIONS["list_runtime_error_signatures"],
    )

    result = await query_structured_runtime_evidence(
        operation="list_runtime_error_signatures",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
        source_key="tmc-prod",
        error_code="SQL-1205",
        exception_class="SQLServerException",
        component_name="tDBOutput_1",
    )

    assert result.status == "succeeded"
    params = seen["params"]
    assert not hasattr(params, "source_key")
    assert params.error_code == "SQL-1205"
    assert params.exception_class == "SQLServerException"
    assert params.component_name == "tDBOutput_1"
    assert "Deadlock victim" in result.items[0].summary


async def test_a_top_n_ranking_returns_n_items_not_a_hidden_ten(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """The evidence serializer used to cap at ten regardless of `limit`, so a
    20-job ranking silently became 10 rows with nothing saying the rest were
    dropped."""

    rows = [
        RuntimeJobReliability(
            project_id="pluripharm",
            source_key="tmc-prod",
            job_name=f"DWH_Load_{index:02d}",
            total_runs=100,
            success_runs=100 - index,
            failed_runs=index,
            unfinished_runs=0,
            failure_rate=index / 100,
            current_failure_streak=index,
        )
        for index in range(20)
    ]
    seen = _record_provider_call(monkeypatch, "list_runtime_job_reliability", rows)

    result = await query_structured_runtime_evidence(
        operation="list_runtime_job_reliability",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
        limit=20,
    )

    assert seen["params"].limit == 20
    assert len(result.items) == 20


async def test_an_unknown_operation_fails_without_touching_the_provider(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    called = False

    async def never_called(_self: Any, _params: Any, _context: Any) -> Any:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        "src.harness.evidence.StoredRuntimeProvider.list_runtime_job_reliability",
        never_called,
    )

    result = await query_structured_runtime_evidence(
        operation="list_runtime_time_of_day",
        project_scope=["pluripharm"],
        db=db_session,
    )

    assert called is False
    assert result.status == "failed"
    assert result.error_message == "The requested runtime operation is not available."
    assert result.items == []


@pytest.mark.parametrize("operation", sorted(_NEW_OPERATIONS))
async def test_a_provider_error_is_a_failed_result_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, operation: str, db_session) -> None:
    method = _RUNTIME_EVIDENCE_OPERATIONS[operation].provider_method

    async def raise_provider_error(_self: Any, _params: Any, _context: Any) -> Any:
        raise RuntimeProviderError("runtime mirror is unavailable")

    monkeypatch.setattr(
        f"src.harness.evidence.StoredRuntimeProvider.{method}", raise_provider_error
    )

    result = await query_structured_runtime_evidence(
        operation=operation,
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
    )

    assert result.status == "failed"
    assert "runtime mirror is unavailable" in (result.error_message or "")


async def test_a_rejected_argument_value_is_a_failed_result_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """Enum coercion and bound validation happen while building the provider
    input, so they have to be inside the same failure contract as the read."""

    called = False

    async def never_called(_self: Any, _params: Any, _context: Any) -> Any:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        "src.harness.evidence.StoredRuntimeProvider.get_runtime_outcome_trend",
        never_called,
    )

    result = await query_structured_runtime_evidence(
        operation="get_runtime_outcome_trend",
        project_scope=["pluripharm"],
        db=db_session,
        project_id="pluripharm",
        bucket="fortnight",
    )

    assert called is False
    assert result.status == "failed"
    assert result.error_message


async def test_the_five_original_operations_still_dispatch(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """The dispatch chain became a table; the operations that already worked
    have to keep working, scope included."""

    original = [
        "list_executions",
        "detect_failures",
        "list_runtime_health",
        "list_runtime_sync_status",
        "list_runtime_sources",
    ]
    for operation in original:
        seen = _record_provider_call(monkeypatch, operation, [{"job_name": "j1"}])
        result = await query_structured_runtime_evidence(
            operation=operation,
            project_scope=["pluripharm", "other-customer"],
            db=db_session,
            project_id="pluripharm",
        )
        assert result.status == "succeeded", operation
        assert seen["scope"] == ["pluripharm"], operation
        assert len(result.items) == 1, operation
