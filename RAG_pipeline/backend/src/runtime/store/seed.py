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
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeLogEvent,
    RuntimeLogSeverity,
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

    for execution in executions:
        shifted = execution.model_copy(
            update={
                "started_at": _shift(execution.started_at, delta),
                "ended_at": _shift(execution.ended_at, delta),
            }
        )
        await repo.upsert_execution(project_id, shifted, source_key, synced_at)

    for failure in failures:
        shifted_failure = failure.model_copy(update={"failed_at": _shift(failure.failed_at, delta)})
        await repo.upsert_failure(project_id, shifted_failure, source_key, synced_at)

    for log in logs:
        shifted_log = log.model_copy(update={"timestamp": _shift(log.timestamp, delta)})
        await repo.upsert_log_event(project_id, shifted_log, source_key, synced_at)

    await repo.update_sync_state(source_key, project_id, synced_at)

    return SeedSummary(
        project_id=project_id,
        source_key=source_key,
        executions=len(executions),
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


async def seed_rich_runtime_data(
    session: AsyncSession,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    source_key: str = DEFAULT_SOURCE_KEY,
    synced_at: datetime.datetime | None = None,
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

    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=_RICH_FAILED_EXEC,
            job_name=_RICH_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.FAILED,
            started_at=failed_start,
            ended_at=synced_at,
            duration_ms=_RICH_RUN_SECONDS * 1000,
        ),
        source_key,
        synced_at,
    )
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=_RICH_SUCCESS_EXEC,
            job_name=_RICH_JOB,
            project_name="esb_main",
            state=RuntimeExecutionState.SUCCESS,
            started_at=success_start,
            ended_at=success_start + datetime.timedelta(seconds=28),
            duration_ms=28_013,
        ),
        source_key,
        synced_at,
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
    return SeedSummary(
        project_id=project_id,
        source_key=source_key,
        executions=2,
        failures=1,
        log_events=log_count,
        last_synced_at=synced_at,
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
                session, project_id=args.project_id, source_key=args.source_key, synced_at=synced_at
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
