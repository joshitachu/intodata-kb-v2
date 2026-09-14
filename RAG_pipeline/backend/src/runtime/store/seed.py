"""Seed the held runtime store from the credential-free mock fixtures.

Loads the existing mock runtime data through the generic provider methods (not by
copying fixture constants) and writes it into the held store via the Slice 1
repository write-port, assigning a demo project and a truthful freshness watermark.

Fixture timestamps are fixed at 2026-01-01; to keep "last 24h" demo questions
meaningful, every timestamp is shifted so the newest fixture event lands at
`synced_at` (current UTC by default, or an explicit value in tests for determinism).

This is an explicit operator/developer action — never auto-run at startup — so it
cannot silently mutate production data. Re-running with the same project_id,
source_key, and synced_at is idempotent (the repository upserts by stable keys).

Usage:
    cd RAG_pipeline/backend && python -m src.runtime.store.seed --project-id demo-runtime
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import sys
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import async_session_factory
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimePlan,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimePlanStep,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.mock import provider as mock_provider
from src.runtime.store import RuntimeStoreRepository

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_KEY = "mock"
DEFAULT_PROJECT_ID = "demo-runtime"

_FETCH_LIMIT = 200
_LOG_FETCH_LIMIT = 1000


@dataclass
class SeedSummary:
    project_id: str
    source_key: str
    executions: int
    failures: int
    log_events: int
    last_synced_at: datetime.datetime


def _shift(ts: datetime.datetime | None, delta: datetime.timedelta) -> datetime.datetime | None:
    return ts + delta if ts is not None else None


async def seed_mock_runtime_data(
    session: AsyncSession,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    source_key: str = DEFAULT_SOURCE_KEY,
    synced_at: datetime.datetime | None = None,
    build_insights: bool = False,
) -> SeedSummary:
    """Load mock runtime fixtures into the held store for `project_id`.

    `synced_at` anchors freshness: the newest fixture event is shifted to it (others
    keep their relative offset). Omitted -> current UTC. Writes go through the
    repository write-port; the watermark is set to `synced_at`.
    """
    if synced_at is None:
        synced_at = datetime.datetime.now(datetime.UTC)

    # Mock ignores context, but the protocol takes one; scope is unrestricted here
    # because seeding reads the full fixture set, not a user-scoped view.
    ctx = RuntimeProviderContext(project_scope=None, db=session)

    executions = await mock_provider.list_executions(ListExecutionsInput(limit=_FETCH_LIMIT), ctx)
    failures = await mock_provider.detect_failures(DetectFailuresInput(limit=_FETCH_LIMIT), ctx)

    logs: list[RuntimeLogEvent] = []
    for execution in executions:
        try:
            logs.extend(
                await mock_provider.get_execution_logs(
                    GetExecutionLogsInput(execution_id=execution.execution_id, limit=_LOG_FETCH_LIMIT), ctx
                )
            )
        except RuntimeProviderError:
            # Execution has no fixture logs — nothing to seed for it.
            continue

    # Anchor the newest fixture timestamp to synced_at, preserving relative ordering.
    candidates: list[datetime.datetime] = []
    for e in executions:
        candidates.append(e.started_at)
        if e.ended_at is not None:
            candidates.append(e.ended_at)
    candidates.extend(f.failed_at for f in failures)
    candidates.extend(log.timestamp for log in logs)
    delta = synced_at - max(candidates) if candidates else datetime.timedelta()

    repo = RuntimeStoreRepository(session)

    await seed_demo_inventory(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
    )

    shifted_executions = [
        execution.model_copy(
            update={
                "started_at": _shift(execution.started_at, delta),
                "ended_at": _shift(execution.ended_at, delta),
            }
        )
        for execution in executions
    ]
    # Already anchored to `synced_at`, so they are appended after the shift.
    # The mock fixtures cannot supply these states (the fixture set predates
    # them and lives in a file this seed only reads), and without them the
    # default demo path shows no misfire and no timeout at all.
    shifted_executions.extend(_demo_capture_state_executions(synced_at))

    for execution in shifted_executions:
        await repo.upsert_execution(project_id, execution, source_key, synced_at)

    await seed_demo_jobs(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
        executions=shifted_executions,
    )
    await seed_demo_plans(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
        executions=shifted_executions,
    )

    for failure in failures:
        shifted_failure = failure.model_copy(update={"failed_at": _shift(failure.failed_at, delta)})
        await repo.upsert_failure(project_id, shifted_failure, source_key, synced_at)

    for log in logs:
        shifted_log = log.model_copy(update={"timestamp": _shift(log.timestamp, delta)})
        await repo.upsert_log_event(project_id, shifted_log, source_key, synced_at)

    await repo.update_sync_state(source_key, project_id, synced_at)

    if build_insights:
        for execution in executions:
            await build_runtime_insights_for_execution(
                session,
                project_id=project_id,
                execution_id=execution.execution_id,
                source_key=source_key,
                ingested_at=synced_at,
            )

    return SeedSummary(
        project_id=project_id,
        source_key=source_key,
        executions=len(shifted_executions),
        failures=len(failures),
        log_events=len(logs),
        last_synced_at=synced_at,
    )


# --- Rich Talend-ESB-style sample (deeper, more realistic logs) ---

_INFO = RuntimeLogSeverity.INFO
_WARN = RuntimeLogSeverity.WARNING
_ERROR = RuntimeLogSeverity.ERROR

_RICH_JOB = "esb_order_sync_route"
_RICH_FAILED_EXEC = "esbmain-route-7841"
_RICH_SUCCESS_EXEC = "esbmain-route-7840"
_RICH_RUN_SECONDS = 32

# (seconds_from_run_start, severity, message) — a Talend ESB route that fails on a
# source DB connection timeout. Component-prefixed lines mirror Talend TEXT logs;
# our model stores them flat in `message` (timestamp/severity are columns).
_RICH_FAILED_LOG_LINES: list[tuple[int, RuntimeLogSeverity, str]] = [
    (0, _INFO, "[esb_order_sync_route] Route starting (build 2026-06-15_14-02, Karaf 4.4.5, JVM 17.0.9)"),
    (1, _INFO, "[cConfig_1] Loaded runtime context 'PROD'"),
    (1, _INFO, "[cMessagingEndpoint_1] Listening on jms:queue:ORDER.INBOUND (concurrentConsumers=4)"),
    (2, _INFO, "[cJMSInput_1] Received message id=ORD-8842193 (1.4 KB) from ORDER.INBOUND"),
    (2, _INFO, "[tOracleConnection_1] Opening connection jdbc:oracle:thin:@//db-prod-scan:1521/ORCLPDB1 (pool=esb_main, timeout=30s)"),
    (7, _WARN, "[tOracleConnection_1] Connection attempt 1/3 failed: IO Error: The Network Adapter could not establish the connection; retrying in 5s"),
    (17, _WARN, "[tOracleConnection_1] Connection attempt 2/3 failed: IO Error: Socket read timed out; retrying in 10s"),
    (18, _INFO, "[tOracleInput_1] Waiting for connection before executing SELECT on ORDERS_STAGING"),
    (30, _ERROR, "[tOracleConnection_1] Connection attempt 3/3 failed after 30000ms: ORA-12170: TNS:Connect timeout occurred"),
    (30, _ERROR, "[tOracleConnection_1] java.sql.SQLRecoverableException: IO Error: Connection timed out\n\tat oracle.jdbc.driver.T4CConnection.<init>(T4CConnection.java:1066)\n\tat oracle.jdbc.driver.T4CDriverExtension.getConnection(T4CDriverExtension.java:33)"),
    (30, _ERROR, "[esb_order_sync_route] Component tOracleConnection_1 raised a fatal error — initiating route shutdown"),
    (31, _WARN, "[cMessagingEndpoint_1] Rolling back in-flight message ORD-8842193 to ORDER.INBOUND.DLQ (redelivery 1/6)"),
    (31, _INFO, "[tOracleInput_1] 0 rows read"),
    (31, _INFO, "[tOracleOutput_1] 0 rows written; transaction rolled back"),
    (31, _ERROR, "[esb_order_sync_route] Route failed: Connection to source database timed out (exit code 1)"),
    (32, _INFO, "[esb_order_sync_route] Route stopped after 31.482s; 0/1 messages processed"),
]

_RICH_SUCCESS_LOG_LINES: list[tuple[int, RuntimeLogSeverity, str]] = [
    (0, _INFO, "[esb_order_sync_route] Route starting (context PROD)"),
    (28, _INFO, "[esb_order_sync_route] Route completed: 184 messages processed in 28.013s"),
]

# --- Runs that produce no logs and no failure row --------------------------
#
# A misfire and a timeout are the two outcomes that are invisible in every
# log-driven surface: the misfire never started, so there is nothing to log,
# and the timeout is killed by the scheduler rather than raising. Both used to
# arrive as `unknown` and were dropped. Seeding them is the only way a demo can
# show the states at all -- and the only way a regression that re-flattens them
# is noticed before a customer sees it.
#
# Values are the ones the live TMC API actually returns.
_MISFIRED_JOB = "esb_inventory_sync_task"
_MISFIRED_EXEC = "esbmain-task-4417"
_TIMED_OUT_JOB = "esb_nightly_reconcile_batch"
_TIMED_OUT_EXEC = "esbmain-batch-5501"
_TIMED_OUT_SECONDS = 3600


def _demo_capture_state_executions(
    synced_at: datetime.datetime,
) -> list[RuntimeExecution]:
    """One misfired and one timed-out run, anchored to `synced_at`."""
    misfired_start = synced_at - datetime.timedelta(minutes=45)
    timed_out_start = synced_at - datetime.timedelta(hours=6)
    return [
        RuntimeExecution(
            execution_id=_MISFIRED_EXEC,
            job_name=_MISFIRED_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.MISFIRED,
            raw_state="EXECUTION_MISFIRED",
            misfire_reason="PARALLEL_EXECUTION_FORBIDDEN",
            execution_type="SCHEDULED",
            started_at=misfired_start,
            # A misfire never ran, so it has no end and no duration. Anything
            # that treats a missing end as "hung" must exclude this state.
            ended_at=None,
            duration_ms=None,
        ),
        RuntimeExecution(
            execution_id=_TIMED_OUT_EXEC,
            job_name=_TIMED_OUT_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.TIMED_OUT,
            raw_state="EXECUTION_TIMEOUT",
            execution_type="SCHEDULED",
            started_at=timed_out_start,
            ended_at=timed_out_start + datetime.timedelta(seconds=_TIMED_OUT_SECONDS),
            duration_ms=_TIMED_OUT_SECONDS * 1000,
        ),
    ]


async def seed_rich_runtime_data(
    session: AsyncSession,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    source_key: str = DEFAULT_SOURCE_KEY,
    synced_at: datetime.datetime | None = None,
    build_insights: bool = False,
) -> SeedSummary:
    """Seed a deeper, realistic Talend-ESB-style failed run (plus a prior success for
    comparison) with an in-depth log sequence. Writes through the repository
    write-port; timestamps anchor to `synced_at` so the failure lands in the last 24h.
    Idempotent for a fixed `synced_at`."""
    if synced_at is None:
        synced_at = datetime.datetime.now(datetime.UTC)

    repo = RuntimeStoreRepository(session)
    failed_start = synced_at - datetime.timedelta(seconds=_RICH_RUN_SECONDS)
    success_start = synced_at - datetime.timedelta(hours=2)

    await seed_demo_inventory(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
    )

    # Built once and then written, rather than declared twice: the previous
    # shape upserted three executions and separately rebuilt the same three for
    # the job/plan seeds, so the two lists could -- and did -- drift from the
    # reported summary count.
    rich_executions = [
        RuntimeExecution(
            execution_id=_RICH_FAILED_EXEC,
            job_name=_RICH_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.FAILED,
            raw_state="EXECUTION_FAILED",
            execution_type="SCHEDULED",
            started_at=failed_start,
            ended_at=synced_at,
            duration_ms=_RICH_RUN_SECONDS * 1000,
        ),
        RuntimeExecution(
            execution_id=_RICH_SUCCESS_EXEC,
            job_name=_RICH_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.SUCCESS,
            raw_state="EXECUTION_SUCCESSFUL",
            execution_type="SCHEDULED",
            started_at=success_start,
            ended_at=success_start + datetime.timedelta(seconds=28),
            duration_ms=28_013,
        ),
        RuntimeExecution(
            execution_id="billing-route-2190",
            job_name="esb_billing_export_route",
            project_name="esb_main",
            state=RuntimeExecutionState.SUCCESS,
            raw_state="EXECUTION_SUCCESSFUL",
            execution_type="MANUAL",
            started_at=synced_at - datetime.timedelta(hours=4),
            ended_at=synced_at - datetime.timedelta(hours=4) + datetime.timedelta(seconds=44),
            duration_ms=44_210,
        ),
        *_demo_capture_state_executions(synced_at),
    ]
    for execution in rich_executions:
        await repo.upsert_execution(project_id, execution, source_key, synced_at)
    await seed_demo_jobs(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
        executions=rich_executions,
    )
    await seed_demo_plans(
        repo,
        project_id=project_id,
        source_key=source_key,
        synced_at=synced_at,
        executions=rich_executions,
    )
    await repo.upsert_failure(
        project_id,
        RuntimeFailure(
            execution_id=_RICH_FAILED_EXEC,
            job_name=_RICH_JOB,
            failed_at=synced_at - datetime.timedelta(seconds=1),
            error_summary="Connection to source database timed out (ORA-12170 after 3 attempts)",
        ),
        source_key,
        synced_at,
    )

    log_count = 0
    for exec_id, start, lines in (
        (_RICH_FAILED_EXEC, failed_start, _RICH_FAILED_LOG_LINES),
        (_RICH_SUCCESS_EXEC, success_start, _RICH_SUCCESS_LOG_LINES),
    ):
        for offset, severity, message in lines:
            await repo.upsert_log_event(
                project_id,
                RuntimeLogEvent(
                    execution_id=exec_id,
                    timestamp=start + datetime.timedelta(seconds=offset),
                    severity=severity,
                    message=message,
                ),
                source_key,
                synced_at,
            )
            log_count += 1

    await repo.update_sync_state(source_key, project_id, synced_at)

    if build_insights:
        for exec_id in (_RICH_FAILED_EXEC, _RICH_SUCCESS_EXEC):
            await build_runtime_insights_for_execution(
                session,
                project_id=project_id,
                execution_id=exec_id,
                source_key=source_key,
                ingested_at=synced_at,
            )

    return SeedSummary(
        project_id=project_id,
        source_key=source_key,
        executions=len(rich_executions),
        failures=1,
        log_events=log_count,
        last_synced_at=synced_at,
    )


async def seed_demo_inventory(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    source_key: str,
    synced_at: datetime.datetime,
) -> None:
    """Seed the minimum runtime estate needed to demo the operational mirror."""
    await repo.upsert_source(
        project_id,
        RuntimeSource(
            project_id=project_id,
            source_key=source_key,
            source_type="mock",
            label="Demo runtime source",
            status=RuntimeSyncStatus.SUCCESS,
            is_demo=True,
            last_synced_at=synced_at,
            last_seen_at=synced_at,
            ingested_at=synced_at,
        ),
        synced_at,
    )
    await repo.upsert_environment(
        project_id,
        RuntimeEnvironment(
            source_key=source_key,
            environment_id="env-prod",
            name="Production",
            description="Demo production runtime environment",
            last_seen_at=synced_at,
        ),
        source_key,
        synced_at,
    )
    await repo.upsert_environment(
        project_id,
        RuntimeEnvironment(
            source_key=source_key,
            environment_id="env-test",
            name="Test",
            description="Demo test runtime environment",
            last_seen_at=synced_at,
        ),
        source_key,
        synced_at,
    )
    await repo.upsert_workspace(
        project_id,
        RuntimeWorkspace(
            source_key=source_key,
            workspace_id="ws-esb-main",
            name="ESB Main",
            environment_id="env-prod",
            environment_name="Production",
            owner="integration-ops",
            workspace_type="operations",
            last_seen_at=synced_at,
        ),
        source_key,
        synced_at,
    )
    await repo.upsert_engine(
        project_id,
        RuntimeEngine(
            source_key=source_key,
            engine_id="engine-prod-01",
            name="remote-engine-prod-01",
            status=RuntimeEngineStatus.ONLINE,
            raw_status="PAIRED",
            availability="AVAILABLE",
            version="8.0.1",
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-esb-main",
            workspace_name="ESB Main",
            last_seen_at=synced_at,
            source_updated_at=synced_at,
        ),
        source_key,
        synced_at,
    )
    await repo.upsert_engine(
        project_id,
        RuntimeEngine(
            source_key=source_key,
            engine_id="engine-prod-02",
            name="remote-engine-prod-02",
            status=RuntimeEngineStatus.DEGRADED,
            raw_status="PAIRED",
            availability="UNAVAILABLE",
            version="8.0.1",
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-esb-main",
            workspace_name="ESB Main",
            last_seen_at=synced_at - datetime.timedelta(minutes=7),
            source_updated_at=synced_at - datetime.timedelta(minutes=7),
        ),
        source_key,
        synced_at,
    )


async def seed_demo_jobs(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    source_key: str,
    synced_at: datetime.datetime,
    executions: list[RuntimeExecution],
) -> None:
    """Seed durable job inventory and job-run facts linked to demo engines."""
    jobs_by_name = {execution.job_name: execution for execution in executions}
    if _RICH_JOB not in jobs_by_name:
        jobs_by_name[_RICH_JOB] = RuntimeExecution(
            execution_id="demo-order-sync-seed",
            job_name=_RICH_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.SUCCESS,
            started_at=synced_at - datetime.timedelta(hours=3),
            ended_at=synced_at - datetime.timedelta(hours=3) + datetime.timedelta(seconds=30),
            duration_ms=30_000,
        )
    if "esb_billing_export_route" not in jobs_by_name:
        jobs_by_name["esb_billing_export_route"] = RuntimeExecution(
            execution_id="demo-billing-export-seed",
            job_name="esb_billing_export_route",
            project_name="esb_main",
            state=RuntimeExecutionState.SUCCESS,
            started_at=synced_at - datetime.timedelta(hours=5),
            ended_at=synced_at - datetime.timedelta(hours=5) + datetime.timedelta(seconds=45),
            duration_ms=45_000,
        )

    job_context = {
        _RICH_JOB: {
            "job_id": "job-order-sync",
            "display_name": "Order Sync Route",
            "engine_id": "engine-prod-02",
            "engine_name": "remote-engine-prod-02",
            "artifact_name": "esb-order-sync-1.8.4",
        },
        "esb_billing_export_route": {
            "job_id": "job-billing-export",
            "display_name": "Billing Export Route",
            "engine_id": "engine-prod-01",
            "engine_name": "remote-engine-prod-01",
            "artifact_name": "esb-billing-export-2.1.0",
        },
    }

    for job_name, execution in sorted(jobs_by_name.items()):
        context = job_context.get(
            job_name,
            {
                "job_id": f"job-{job_name.replace('_', '-')[:80]}",
                "display_name": job_name.replace("_", " ").title(),
                "engine_id": "engine-prod-01",
                "engine_name": "remote-engine-prod-01",
                "artifact_name": None,
            },
        )
        await repo.upsert_job(
            project_id,
            RuntimeJob(
                source_key=source_key,
                job_id=context["job_id"],
                name=job_name,
                display_name=context["display_name"],
                job_type="task",
                status="active",
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-esb-main",
                workspace_name="ESB Main",
                engine_id=context["engine_id"],
                engine_name=context["engine_name"],
                owner="integration-ops",
                artifact_name=context["artifact_name"],
                last_seen_at=execution.ended_at or execution.started_at,
                source_updated_at=synced_at,
            ),
            source_key,
            synced_at,
        )

    for execution in executions:
        context = job_context.get(
            execution.job_name,
            {
                "job_id": f"job-{execution.job_name.replace('_', '-')[:80]}",
                "engine_id": "engine-prod-01",
                "engine_name": "remote-engine-prod-01",
            },
        )
        await repo.upsert_job_run(
            project_id,
            RuntimeJobRun(
                source_key=source_key,
                run_id=execution.execution_id,
                job_id=context["job_id"],
                job_name=execution.job_name,
                execution_id=execution.execution_id,
                state=execution.state,
                # Carried, not re-derived: the job-run mirror is what the
                # operations surfaces read, so dropping the evidence here would
                # leave the demo showing a bare state with no reason.
                raw_state=execution.raw_state,
                misfire_reason=execution.misfire_reason,
                execution_type=execution.execution_type,
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-esb-main",
                workspace_name="ESB Main",
                engine_id=context["engine_id"],
                engine_name=context["engine_name"],
                started_at=execution.started_at,
                ended_at=execution.ended_at,
                duration_ms=execution.duration_ms,
            ),
            source_key,
            synced_at,
        )


async def seed_demo_plans(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    source_key: str,
    synced_at: datetime.datetime,
    executions: list[RuntimeExecution],
) -> None:
    """Seed a generic orchestration plan composed of the demo jobs."""
    executions_by_id = {execution.execution_id: execution for execution in executions}
    failed_order = executions_by_id.get(_RICH_FAILED_EXEC)
    success_order = executions_by_id.get(_RICH_SUCCESS_EXEC)
    billing = executions_by_id.get("billing-route-2190")

    steps = [
        RuntimePlanStep(
            step_id="step-order-sync",
            name="Run order sync",
            job_id="job-order-sync",
            job_name=_RICH_JOB,
            order=1,
        ),
        RuntimePlanStep(
            step_id="step-billing-export",
            name="Run billing export",
            job_id="job-billing-export",
            job_name="esb_billing_export_route",
            order=2,
        ),
    ]

    await repo.upsert_plan(
        project_id,
        RuntimePlan(
            source_key=source_key,
            plan_id="plan-nightly-order-flow",
            name="nightly_order_flow",
            display_name="Nightly Order Flow",
            status="active",
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-esb-main",
            workspace_name="ESB Main",
            owner="integration-ops",
            step_count=len(steps),
            steps=steps,
            last_seen_at=synced_at,
            source_updated_at=synced_at,
        ),
        source_key,
        synced_at,
    )

    failed_start = failed_order.started_at if failed_order is not None else synced_at - datetime.timedelta(minutes=2)
    await repo.upsert_plan_run(
        project_id,
        RuntimePlanRun(
            source_key=source_key,
            run_id="plan-nightly-order-flow-7841",
            plan_id="plan-nightly-order-flow",
            plan_name="nightly_order_flow",
            state=RuntimeExecutionState.FAILED,
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-esb-main",
            workspace_name="ESB Main",
            started_at=failed_start,
            ended_at=synced_at,
            duration_ms=int((synced_at - failed_start).total_seconds() * 1000),
            step_results=[
                RuntimePlanRunStepResult(
                    step_id="step-order-sync",
                    job_id="job-order-sync",
                    job_name=_RICH_JOB,
                    state=RuntimeExecutionState.FAILED,
                    execution_id=failed_order.execution_id if failed_order is not None else None,
                ),
                RuntimePlanRunStepResult(
                    step_id="step-billing-export",
                    job_id="job-billing-export",
                    job_name="esb_billing_export_route",
                    state=RuntimeExecutionState.UNKNOWN,
                    execution_id=None,
                ),
            ],
        ),
        source_key,
        synced_at,
    )

    success_start = (success_order.started_at if success_order is not None else synced_at - datetime.timedelta(hours=2))
    success_end = (
        (billing.ended_at if billing is not None else None)
        or (success_order.ended_at if success_order is not None else None)
        or success_start + datetime.timedelta(minutes=1)
    )
    await repo.upsert_plan_run(
        project_id,
        RuntimePlanRun(
            source_key=source_key,
            run_id="plan-nightly-order-flow-7840",
            plan_id="plan-nightly-order-flow",
            plan_name="nightly_order_flow",
            state=RuntimeExecutionState.SUCCESS,
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-esb-main",
            workspace_name="ESB Main",
            started_at=success_start,
            ended_at=success_end,
            duration_ms=int((success_end - success_start).total_seconds() * 1000),
            step_results=[
                RuntimePlanRunStepResult(
                    step_id="step-order-sync",
                    job_id="job-order-sync",
                    job_name=_RICH_JOB,
                    state=RuntimeExecutionState.SUCCESS,
                    execution_id=success_order.execution_id if success_order is not None else None,
                ),
                RuntimePlanRunStepResult(
                    step_id="step-billing-export",
                    job_id="job-billing-export",
                    job_name="esb_billing_export_route",
                    state=RuntimeExecutionState.SUCCESS,
                    execution_id=billing.execution_id if billing is not None else None,
                ),
            ],
        ),
        source_key,
        synced_at,
    )
async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the held runtime store from mock fixtures.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--synced-at", default=None, help="ISO-8601 UTC timestamp; defaults to now.")
    parser.add_argument(
        "--rich-logs",
        action="store_true",
        help="Seed the deeper Talend-ESB-style sample (realistic multi-line failure logs) "
        "instead of the basic mock fixtures.",
    )
    parser.add_argument(
        "--build-insights",
        action="store_true",
        help="Also build deterministic insight rows (enriched events, summaries, incidents) "
        "after seeding raw runtime data.",
    )
    args = parser.parse_args()

    synced_at = None
    if args.synced_at:
        synced_at = datetime.datetime.fromisoformat(args.synced_at)
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=datetime.UTC)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    seed_fn = seed_rich_runtime_data if args.rich_logs else seed_mock_runtime_data

    async with async_session_factory() as session:
        try:
            summary = await seed_fn(
                session,
                project_id=args.project_id,
                source_key=args.source_key,
                synced_at=synced_at,
                build_insights=args.build_insights,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Runtime seed failed — rolled back.")
            sys.exit(1)

    logger.info(
        "Seeded runtime store: project=%s source=%s executions=%d failures=%d logs=%d as_of=%s",
        summary.project_id,
        summary.source_key,
        summary.executions,
        summary.failures,
        summary.log_events,
        summary.last_synced_at.isoformat(),
    )


if __name__ == "__main__":
    asyncio.run(main())
