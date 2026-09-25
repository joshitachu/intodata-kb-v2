"""Read-only: what does TMC serve for a clustered Route, per engine?

One route task, deployed to two remote engines, is ONE task with one execution
per engine. Engine PRD_RE_01 yields logs; PRD_RE_02 yields totalSize == 0. This
probe asks whether the exchange counters are reachable WITHOUT the log, by
dumping the structure TMC returns for both engines' executions and sweeping
candidate metric endpoints.

String values are truncated to 60 chars; this lane carries metadata only, but
the estate processes dispensing data so nothing is printed in full.

Usage (inside the backend container):
    python - <task_id> <source_id>
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from sqlalchemy import select

from src.db.models.runtime_data import RuntimeSourceRow
from src.db.session import async_session_factory
from src.runtime.store.repository import RuntimeStoreRepository


def _shrink(value, depth=0):
    if isinstance(value, dict):
        return {k: _shrink(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v, depth + 1) for v in value[:3]] + (
            [f"...+{len(value) - 3} more"] if len(value) > 3 else []
        )
    if isinstance(value, str) and len(value) > 60:
        return value[:60] + f"...({len(value)} chars)"
    return value


async def main() -> None:
    task_id = sys.argv[1]
    source_id = int(sys.argv[2])
    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _ = await repo.read_credentials(source_id)
        source = (
            await session.execute(select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id))
        ).scalar_one()
    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    print(f"source {source.source_key} base {base_url} task {task_id}\n")

    async with httpx.AsyncClient(
        base_url=base_url, timeout=60.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        print("### /processing/executables/tasks/{task}/executions")
        r = await client.get(f"/processing/executables/tasks/{task_id}/executions")
        print(f"HTTP {r.status_code}")
        if r.status_code < 400:
            body = r.json()
            items = body.get("items", body) if isinstance(body, dict) else body
            for item in items if isinstance(items, list) else []:
                print(json.dumps(_shrink(item), indent=2, default=str))
                print("-" * 60)

        print("\n### /processing/executables/tasks/{task}")
        r = await client.get(f"/processing/executables/tasks/{task_id}")
        print(f"HTTP {r.status_code}")
        if r.status_code < 400:
            print(json.dumps(_shrink(r.json()), indent=2, default=str))

        candidates = [
            f"/processing/executables/tasks/{task_id}/metrics",
            f"/monitoring/executables/tasks/{task_id}/metrics",
            f"/processing/executables/routes/{task_id}",
            f"/monitoring/routes/{task_id}/metrics",
            "/processing/runtimes/remote-engines",
        ]
        print("\n### endpoint sweep")
        for path in candidates:
            try:
                resp = await client.get(path, params={"limit": 1})
                head = ""
                if resp.status_code < 400:
                    body = resp.json()
                    head = (
                        ",".join(sorted(body.keys()))[:120]
                        if isinstance(body, dict)
                        else f"list[{len(body)}]"
                    )
                print(f"  HTTP {resp.status_code:<4} {path}   {head}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR  {type(exc).__name__:<18} {path}")


if __name__ == "__main__":
    asyncio.run(main())
