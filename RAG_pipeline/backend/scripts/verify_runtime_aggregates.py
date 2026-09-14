"""Prove the SQL-aggregated runtime reads return what the Python reduce returned.

Loads the pre-refactor repository modules side by side with the current ones and
compares their output for the same inputs, inside ONE `REPEATABLE READ`
transaction so a live mirror sync cannot be mistaken for a behaviour change.

Run INSIDE the backend container, with the baseline sources copied in:

    docker exec -w /app -e PYTHONPATH=/app intodata-kb-v2-backend-1 \
        python /tmp/verify_runtime_aggregates.py \
            --legacy-store /tmp/baseline/store_repository.py \
            --legacy-insights /tmp/baseline/insights_repository.py

Exit code 0 means every compared surface matched.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from typing import Any

from sqlalchemy import select

from src.db.models.runtime_data import RuntimeSourceRow
from src.db.session import async_session_factory
from src.runtime.insights.repository import RuntimeInsightRepository
from src.runtime.models import (
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
)
from src.runtime.store.repository import RuntimeStoreRepository


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _dump(value: Any):
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _compare(label: str, legacy: Any, current: Any, failures: list[str]) -> None:
    left, right = _dump(legacy), _dump(current)
    if left == right:
        count = len(left) if isinstance(left, list) else 1
        print(f"  OK    {label}  ({count} row(s))")
        return
    failures.append(label)
    print(f"  DIFF  {label}")
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            print(f"        length {len(left)} -> {len(right)}")
        for index, (a, b) in enumerate(zip(left, right)):
            if a != b:
                keys = sorted(
                    key for key in set(a) | set(b) if a.get(key) != b.get(key)
                )
                print(f"        [{index}] {[(k, a.get(k), b.get(k)) for k in keys]}")
    else:
        keys = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
        print(f"        {[(k, left.get(k), right.get(k)) for k in keys]}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-store", required=True)
    parser.add_argument("--legacy-insights", required=True)
    args = parser.parse_args()

    legacy_store = _load(args.legacy_store, "legacy_store_repository")
    legacy_insights = _load(args.legacy_insights, "legacy_insights_repository")

    failures: list[str] = []
    async with async_session_factory() as session:
        # Freeze the snapshot: the mirror is polled continuously, so without this
        # a row arriving between the two calls reads as a behaviour change.
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ"}
        )
        rows = (await session.execute(select(RuntimeSourceRow))).scalars().all()
        pairs = sorted({(row.project_id, row.source_key) for row in rows})
        print(f"sources under test: {pairs}")

        legacy = legacy_store.RuntimeStoreRepository(session)
        current = RuntimeStoreRepository(session)
        legacy_ins = legacy_insights.RuntimeInsightRepository(session)
        current_ins = RuntimeInsightRepository(session)

        scopes: list[tuple[str, Any]] = [("superadmin", None)]
        scopes += [(f"scope[{pid}]", [pid]) for pid in sorted({p for p, _ in pairs})]
        scopes.append(("denied", []))

        for scope_label, scope in scopes:
            variants: list[tuple[str, dict]] = [("bare", {})]
            for project_id, source_key in pairs:
                variants.append((f"project={project_id}", {"project_id": project_id}))
                variants.append(
                    (
                        f"project={project_id},source={source_key}",
                        {"project_id": project_id, "source_key": source_key},
                    )
                )
            for variant_label, kwargs in variants:
                tag = f"{scope_label} {variant_label}"

                _compare(
                    f"get_estate_overview        [{tag}]",
                    await legacy.get_estate_overview(
                        scope, ListRuntimeInventoryInput(**kwargs)
                    ),
                    await current.get_estate_overview(
                        scope, ListRuntimeInventoryInput(**kwargs)
                    ),
                    failures,
                )
                for attention in (False, True):
                    suffix = f"attention={attention}"
                    _compare(
                        f"list_engine_health  {suffix} [{tag}]",
                        await legacy.list_engine_health(
                            scope,
                            ListRuntimeEnginesInput(**kwargs),
                            attention_only=attention,
                        ),
                        await current.list_engine_health(
                            scope,
                            ListRuntimeEnginesInput(**kwargs),
                            attention_only=attention,
                        ),
                        failures,
                    )
                    _compare(
                        f"list_job_health     {suffix} [{tag}]",
                        await legacy.list_job_health(
                            scope,
                            ListRuntimeJobsInput(**kwargs),
                            attention_only=attention,
                        ),
                        await current.list_job_health(
                            scope,
                            ListRuntimeJobsInput(**kwargs),
                            attention_only=attention,
                        ),
                        failures,
                    )
                    _compare(
                        f"list_plan_health    {suffix} [{tag}]",
                        await legacy.list_plan_health(
                            scope,
                            ListRuntimePlansInput(**kwargs),
                            attention_only=attention,
                        ),
                        await current.list_plan_health(
                            scope,
                            ListRuntimePlansInput(**kwargs),
                            attention_only=attention,
                        ),
                        failures,
                    )
                for limit in (50, 200):
                    _compare(
                        f"list_health_summaries limit={limit} [{tag}]",
                        await legacy.list_health_summaries(scope, limit=limit, **kwargs),
                        await current.list_health_summaries(scope, limit=limit, **kwargs),
                        failures,
                    )

            for limit, offset in ((50, 0), (25, 10), (200, 0)):
                _compare(
                    f"list_incidents limit={limit} offset={offset} [{scope_label}]",
                    await legacy_ins.list_incidents(scope, limit=limit, offset=offset),
                    await current_ins.list_incidents(scope, limit=limit, offset=offset),
                    failures,
                )

        # Point lookups share the batch loader now; check one against the legacy path.
        incidents = await current_ins.list_incidents(None, limit=5)
        for incident in incidents:
            _compare(
                f"get_incident {incident.incident_id}",
                await legacy_ins.get_incident(None, incident.incident_id),
                await current_ins.get_incident(None, incident.incident_id),
                failures,
            )
            _compare(
                f"get_execution_summary {incident.execution_id}",
                await legacy_ins.get_execution_summary(None, incident.execution_id),
                await current_ins.get_execution_summary(None, incident.execution_id),
                failures,
            )

    print()
    if failures:
        print(f"FAILED: {len(failures)} surface(s) differ")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("ALL SURFACES MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
