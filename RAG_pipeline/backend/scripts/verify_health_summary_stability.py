"""Was the old `/health` tail order a property of the data, or of the query plan?

`list_health_summaries` sorts by (failure_rate, streak, recurrence, last_failure_at)
with a stable sort, so every group sharing one key keeps the order it was first
seen in during an UNORDERED `select(RuntimeExecutionRow)`. That is heap-fetch
order, which SQL does not define.

This forces a parallel sequential scan -- same rows, same snapshot, an
interleaved fetch order -- and reports whether each implementation's output
survives it. The SQL rollup has an explicit ORDER BY and must; the Python reduce
has nothing to hold it in place.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys

from sqlalchemy import text

from src.db.session import async_session_factory
from src.runtime.store.repository import RuntimeStoreRepository

BIG = 1_000_000

_FORCE_PARALLEL = (
    "SET LOCAL max_parallel_workers_per_gather = 4",
    "SET LOCAL parallel_setup_cost = 0",
    "SET LOCAL parallel_tuple_cost = 0",
    "SET LOCAL min_parallel_table_scan_size = 0",
)


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-store", required=True)
    args = parser.parse_args()
    legacy_module = _load(args.legacy_store, "legacy_store_repository")

    async with async_session_factory() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        repos = {
            "legacy (Python reduce)": legacy_module.RuntimeStoreRepository(session),
            "current (SQL rollup)": RuntimeStoreRepository(session),
        }
        default = {
            label: [r.job_name for r in await repo.list_health_summaries(None, limit=BIG)]
            for label, repo in repos.items()
        }
        for statement in _FORCE_PARALLEL:
            await session.execute(text(statement))
        forced = {
            label: [r.job_name for r in await repo.list_health_summaries(None, limit=BIG)]
            for label, repo in repos.items()
        }
        for label in repos:
            same_set = sorted(default[label]) == sorted(forced[label])
            same_order = default[label] == forced[label]
            print(f"{label}:")
            print(f"  groups: {len(default[label])}")
            print(f"  same set under a forced parallel scan:   {same_set}")
            print(f"  same order under a forced parallel scan: {same_order}")
            if not same_order:
                first = next(
                    i
                    for i, (a, b) in enumerate(zip(default[label], forced[label]))
                    if a != b
                )
                print(
                    f"  first order divergence at index {first}: "
                    f"{default[label][first]!r} vs {forced[label][first]!r}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
