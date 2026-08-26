"""Read-only probe: what status strings does TMC actually return for task runs?

Our ``_STATE_MAP`` only knows EXECUTION_SUCCESSFUL/FAILED/RUNNING, and every
other value collapses to ``unknown``. We do not persist the raw status, so the
only way to learn the real vocabulary is to ask TMC. Prints distinct status
values with counts -- never the token, never a log line, never a payload body.

NOT RUN against any live source. Prefer the in-pipeline fallthrough warning in
``_normalize_state``, which surfaces unmapped values during a normal sync cycle
without a manual probe.

Usage (inside the backend container):
    python -m scripts.probe_tmc_statuses <source_id> [task_id ...]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

import httpx

from src.db.session import async_session_factory
from src.runtime.store.repository import RuntimeStoreRepository


async def main() -> None:
    source_id = int(sys.argv[1])
    task_ids = sys.argv[2:]

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _key_id = await repo.read_credentials(source_id)

    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token or not base_url:
        raise SystemExit("source has no usable TMC credentials")

    async with httpx.AsyncClient(
        base_url=base_url, timeout=30.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        for task_id in task_ids:
            counts: Counter[str] = Counter()
            samples: dict[str, str] = {}
            response = await client.get(
                f"/processing/executables/tasks/{task_id}/executions",
                params={"limit": 200, "offset": 0},
            )
            response.raise_for_status()
            payload = response.json()
            items = payload if isinstance(payload, list) else payload.get("items", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or item.get("executionStatus") or "<none>")
                counts[status] += 1
                samples.setdefault(
                    status,
                    f"start={item.get('startTimestamp')} finish={item.get('finishTimestamp')}",
                )
            print(f"\ntask {task_id}: {len(items)} executions")
            for status, count in counts.most_common():
                print(f"  {status:<28} {count:>4}   e.g. {samples[status]}")


if __name__ == "__main__":
    asyncio.run(main())
