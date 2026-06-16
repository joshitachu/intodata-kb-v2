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
    RuntimeFailure,
    RuntimeLogEvent,
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the held runtime store from mock fixtures.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--synced-at", default=None, help="ISO-8601 UTC timestamp; defaults to now.")
    args = parser.parse_args()

    synced_at = None
    if args.synced_at:
        synced_at = datetime.datetime.fromisoformat(args.synced_at)
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=datetime.UTC)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async with async_session_factory() as session:
        try:
            summary = await seed_mock_runtime_data(
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
