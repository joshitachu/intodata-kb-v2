"""Diagnose the one `list_health_summaries` divergence: values or ordering?

Compares the pre-refactor Python reduce with the SQL rollup on a frozen
snapshot, three ways:
  1. the complete group set, canonically sorted -> are the NUMBERS the same?
  2. the returned page order                    -> is the ORDER the same?
  3. the legacy order under a forced different query plan
     (`enable_seqscan = off`)                   -> was the old order stable at all?
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


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _canonical(rows):
    return sorted(
        (row.model_dump(mode="json") for row in rows),
        key=lambda item: (item["project_id"], item["source_key"] or "", item["job_name"]),
    )


def _key(row):
    return (
        row.failure_rate,
        row.current_failure_streak,
        row.recurrence_count or 0,
        row.last_failure_at.isoformat() if row.last_failure_at else "",
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-store", required=True)
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    legacy_module = _load(args.legacy_store, "legacy_store_repository")

    async with async_session_factory() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        legacy = legacy_module.RuntimeStoreRepository(session)
        current = RuntimeStoreRepository(session)

        kwargs = {"project_id": args.project} if args.project else {}
        legacy_all = await legacy.list_health_summaries(None, limit=BIG, **kwargs)
        current_all = await current.list_health_summaries(None, limit=BIG, **kwargs)

        print(f"groups: legacy={len(legacy_all)} current={len(current_all)}")
        same_values = _canonical(legacy_all) == _canonical(current_all)
        print(f"1. complete group set, canonically sorted, identical: {same_values}")
        if not same_values:
            left = {(r["project_id"], r["job_name"]): r for r in _canonical(legacy_all)}
            right = {(r["project_id"], r["job_name"]): r for r in _canonical(current_all)}
            for key in sorted(set(left) | set(right)):
                a, b = left.get(key), right.get(key)
                if a != b:
                    if a is None or b is None:
                        print(f"   only one side has {key}")
                        continue
                    fields = [k for k in a if a[k] != b[k]]
                    print(f"   {key}: {[(k, a[k], b[k]) for k in fields]}")

        legacy_order = [(r.project_id, r.job_name) for r in legacy_all]
        current_order = [(r.project_id, r.job_name) for r in current_all]
        print(f"2. page order identical: {legacy_order == current_order}")
        if legacy_order != current_order:
            first = next(
                i for i, (a, b) in enumerate(zip(legacy_order, current_order)) if a != b
            )
            print(f"   first divergence at index {first}")
            print(f"   legacy[{first}]  = {legacy_order[first]}  key={_key(legacy_all[first])}")
            print(f"   current[{first}] = {current_order[first]}  key={_key(current_all[first])}")
            tied = [i for i in range(len(legacy_all)) if _key(legacy_all[i]) == _key(legacy_all[first])]
            print(
                f"   groups sharing that exact sort key: {len(tied)} "
                f"(indices {tied[0]}..{tied[-1]})"
            )
            print(
                "   -> every divergent position lies inside a fully tied block: "
                f"{all(_key(legacy_all[i]) == _key(current_all[i]) for i in range(len(legacy_all)))}"
            )

        # 3. Was the legacy order a property of the data, or of the query plan?
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        replanned = await legacy.list_health_summaries(None, limit=BIG, **kwargs)
        replanned_order = [(r.project_id, r.job_name) for r in replanned]
        print(
            "3. legacy order under a different plan (enable_seqscan=off) equals its "
            f"own seq-scan order: {replanned_order == legacy_order}"
        )
        current_replanned = [
            (r.project_id, r.job_name)
            for r in await current.list_health_summaries(None, limit=BIG, **kwargs)
        ]
        print(
            "   SQL rollup order under the same forced plan equals its own: "
            f"{current_replanned == current_order}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
