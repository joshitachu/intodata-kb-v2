"""Seed a controllable runtime-health scenario for the topology overlay demo (S3).

The topology runtime-health overlay colours each job node by its live runtime
health and propagates a `down` child's failure up to every structural caller
(`impacted`). The only scanned project with real job->job dependencies that is
useful for a demo is `reverse_engineer_VW`, but it has no runtime data in the
store. This script seeds a deterministic scenario so the overlay lights up:

    AssetCheck_Main_Job (HEALTHY own status)
      -> AssetCheck_GetLocation      (DOWN     - latest run failed, 3-run streak)
      -> AssetCheck_GetWorkOrders    (DEGRADED - failure_rate >= 0.3, latest ok)
      -> AssetCheck_SendServiceRequest, AssetCheck_GetLocations,
         AssetCheck_SendWorkOrderUpdate, AssetCheck_GetWorkOrder  (HEALTHY)
    Maximo2AssetCheck_MainJob + children (all HEALTHY)

Because AssetCheck_Main_Job's OWN runs all succeed, the only reason it turns
red/impacted in the UI is propagation from its down child - which is exactly
what proves the propagation logic rather than plain node colouring.

This is legitimate test data written into the held runtime store via the
repository write-port (so timestamps/watermarks are set exactly as a live sync
would). It is never fabricated on the UI side; the endpoint still returns only
what is in the store.

Job names are seeded EXACTLY as they appear in the scan manifest so the UI's
identity join matches on the exact tier.

Idempotent: re-running with the same project_id/source_key upserts by stable
keys. Run against the live containerised DB, e.g.:

    docker exec intodata-kb-v2-backend-1 python -m scripts.seed_topology_health_demo

or locally:

    cd RAG_pipeline/backend && python -m scripts.seed_topology_health_demo
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import sys

from src.db.session import async_session_factory
from src.runtime.models import (
    ListRuntimeJobsInput,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
)
from src.runtime.store import RuntimeStoreRepository

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_ID = "reverse_engineer_VW"
DEFAULT_SOURCE_KEY = "demo-topology"

# state, minutes-ago pairs per job, newest first. `latest_state` and the
# failure streak are read from the newest run by started_at.
S = RuntimeExecutionState.SUCCESS
F = RuntimeExecutionState.FAILED

# Each entry: job_name -> list of (state, minutes_ago). Job names are the exact
# scanner names from reverse_engineer_VW/scan/manifest.json.
_HEALTHY = [(S, 12), (S, 180), (S, 420)]

JOB_RUNS: dict[str, list[tuple[RuntimeExecutionState, int]]] = {
    # DOWN: latest failed with a 3-run failure streak (failure_rate 3/5 = 0.6).
    "AssetCheck_GetLocation": [(F, 8), (F, 41), (F, 73), (S, 190), (S, 320)],
    # DEGRADED: latest run succeeded but failure_rate 2/5 = 0.4 >= 0.3.
    "AssetCheck_GetWorkOrders": [(S, 15), (F, 46), (F, 120), (S, 240), (S, 360)],
    # HEALTHY caller — turns red ONLY via propagation from AssetCheck_GetLocation.
    "AssetCheck_Main_Job": list(_HEALTHY),
    "AssetCheck_SendServiceRequest": list(_HEALTHY),
    "AssetCheck_GetLocations": list(_HEALTHY),
    "AssetCheck_SendWorkOrderUpdate": list(_HEALTHY),
    "AssetCheck_GetWorkOrder": list(_HEALTHY),
    # Second graph — all healthy, demonstrates no false propagation.
    "Maximo2AssetCheck_MainJob": list(_HEALTHY),
    "Maximo2AssetCheck_SendLocation": list(_HEALTHY),
    "Maximo2AssetCheck_SendWorkOrder": list(_HEALTHY),
}


def _job_id(job_name: str) -> str:
    return f"job-{job_name.replace('_', '-')[:80]}"


async def seed_topology_health_demo(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    source_key: str,
    synced_at: datetime.datetime,
) -> int:
    """Upsert the demo job inventory + run facts. Returns the number of runs written."""
    total_runs = 0
    for job_name, runs in JOB_RUNS.items():
        job_id = _job_id(job_name)
        newest_offset = min(offset for _state, offset in runs)
        last_seen = synced_at - datetime.timedelta(minutes=newest_offset)

        await repo.upsert_job(
            project_id,
            RuntimeJob(
                source_key=source_key,
                job_id=job_id,
                name=job_name,
                display_name=job_name.replace("_", " "),
                job_type="task",
                status="active",
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-reverse-engineer-vw",
                workspace_name="Reverse Engineer VW",
                engine_id="engine-demo-01",
                engine_name="remote-engine-demo-01",
                owner="integration-ops",
                artifact_name=None,
                last_seen_at=last_seen,
                source_updated_at=synced_at,
            ),
            source_key,
            synced_at,
        )

        for index, (state, minutes_ago) in enumerate(runs):
            started_at = synced_at - datetime.timedelta(minutes=minutes_ago)
            ended_at = started_at + datetime.timedelta(seconds=30)
            await repo.upsert_job_run(
                project_id,
                RuntimeJobRun(
                    source_key=source_key,
                    run_id=f"{job_id}-run-{index}",
                    job_id=job_id,
                    job_name=job_name,
                    execution_id=f"{job_id}-run-{index}",
                    state=state,
                    environment_id="env-prod",
                    environment_name="Production",
                    workspace_id="ws-reverse-engineer-vw",
                    workspace_name="Reverse Engineer VW",
                    engine_id="engine-demo-01",
                    engine_name="remote-engine-demo-01",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=30_000,
                ),
                source_key,
                synced_at,
            )
            total_runs += 1

    return total_runs


async def _report(repo: RuntimeStoreRepository, project_id: str) -> None:
    """Print the derived health the API will return (scope=None = superadmin view)."""
    health = await repo.list_job_health(
        None, ListRuntimeJobsInput(project_id=project_id, limit=100)
    )
    logger.info("Derived job-health for project '%s':", project_id)
    for row in sorted(health, key=lambda r: r.job_name):
        logger.info(
            "  %-32s latest=%-8s failed=%d/%d rate=%.2f streak=%d",
            row.job_name,
            row.latest_state.value if row.latest_state else "none",
            row.failed_runs,
            row.total_runs,
            row.failure_rate,
            row.current_failure_streak,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    synced_at = datetime.datetime.now(datetime.UTC)

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        try:
            runs = await seed_topology_health_demo(
                repo,
                project_id=args.project_id,
                source_key=args.source_key,
                synced_at=synced_at,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Topology health demo seed failed — rolled back.")
            sys.exit(1)

        logger.info(
            "Seeded topology health demo: project=%s source=%s jobs=%d runs=%d as_of=%s",
            args.project_id,
            args.source_key,
            len(JOB_RUNS),
            runs,
            synced_at.isoformat(),
        )
        await _report(repo, args.project_id)


if __name__ == "__main__":
    asyncio.run(main())
