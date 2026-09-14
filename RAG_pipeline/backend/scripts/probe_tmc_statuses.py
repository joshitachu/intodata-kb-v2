"""Read-only probe: what status strings does TMC actually return for task runs?

Prints the status vocabulary and the field inventory, split by executable kind
-- never the token, never a log line, never a payload body.

Day to day you should NOT need this: ``_normalize_state`` warns on any status it
cannot map, so a novel TMC value surfaces during a normal sync. The probe earns
its keep for the questions that warning cannot answer -- which FIELDS arrive and
which of them we drop, and whether Routes and Data Services differ from Jobs.
They do: Routes are the larger half of a real estate, their dominant status is
``executing`` (a healthy long-lived deployment, not a batch outcome), and their
per-execution field fill rates are nothing like a Job's.

Two API constraints, both learned the hard way:
- ``/processing/executables/tasks/{id}/executions`` requires a ``from``/``to``
  epoch-ms window; a bare ``limit``/``offset`` call is an HTTP 400.
- ``limit`` is capped at 100. A larger value is a 400, not a clamp.
- Task ids are tenant-scoped: an id mirrored from another source 404s here,
  which is why the estate is discovered from the token rather than the mirror.

Usage (inside the backend container):
    python -m scripts.probe_tmc_statuses <source_id> <days> [task_id ...]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from src.db.session import async_session_factory
from src.runtime.providers.talend_tmc import _STATE_MAP, _STATE_RESOLVERS
from src.runtime.store.repository import RuntimeStoreRepository


# Read the live mapping out of the provider rather than restating it. A
# hand-copied set is what let this probe claim `deploy_failed` was understood
# for as long as it was, and a probe that lies about the baseline is worse than
# no probe. Importing the private names is deliberate: they ARE the contract
# this script measures against.
MAPPED = set(_STATE_MAP) | set(_STATE_RESOLVERS)

# Fields the ingest reader consumes today (talend_tmc.py `_to_job_run`,
# `_failure_summary_for_task_run`). Everything else TMC returns is dropped.
CONSUMED = {
    "executionId", "execution_id", "id", "taskId", "task_id",
    "status", "executionStatus", "state",
    "misfiredExecutionReason", "executionType",
    "startTimestamp", "startTime", "startedAt", "triggerTimestamp",
    "finishTimestamp", "finishTime", "endedAt",
    "runtime", "errorMessage", "error_message", "errorDetails", "error_details",
    "completedExchanges", "failedExchanges", "totalExchanges",
    "lastProcessTime", "meanProcessTime", "Count", "count",
    "OneMinuteRate", "FiveMinuteRate", "FifteenMinuteRate",
}

# Only these are echoed as sample values, so the probe cannot print business
# data. Everything else is reported as a field name and a fill count.
SAFE_TO_ECHO = {
    "status", "executionStatus", "state", "executionType", "triggerType",
    "errorType", "errorCode", "exitCode", "misfiredExecutionReason",
    "timeout", "parallelExecutionAllowed",
}


def summarize(items: list[dict], label: str) -> tuple[Counter, Counter, Counter]:
    statuses: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    unmapped_ctx: Counter[str] = Counter()
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or item.get("executionStatus") or "<none>")
        statuses[status] += 1
        for key, value in item.items():
            if value is not None and value != "":
                fields[key] += 1
        # Cross-tab every status against the misfire reason: the question is
        # not just which statuses exist, but which of them carry the field that
        # explains a schedule that never produced a run.
        reason = item.get("misfiredExecutionReason")
        if status.lower() not in MAPPED or reason:
            ctx = {k: v for k, v in item.items() if k in SAFE_TO_ECHO}
            unmapped_ctx[f"{status} :: {ctx}"] += 1
    return statuses, fields, unmapped_ctx


async def main() -> None:
    source_id = int(sys.argv[1])
    days = int(sys.argv[2])
    task_ids = sys.argv[3:]

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _key_id = await repo.read_credentials(source_id)

    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token or not base_url:
        raise SystemExit("source has no usable TMC credentials")

    now = datetime.now(timezone.utc)
    window = {
        "from": int((now - timedelta(days=days)).timestamp() * 1000),
        "to": int(now.timestamp() * 1000),
    }

    # Routes and Data Services are TMC *tasks* too -- the kind only shows up on
    # the linked artifact, so a probe that ignores artifact type silently reports
    # a job-only picture. Mirrors `_executable_kind` in the provider.
    by_kind: dict[str, Counter] = {}
    fields_by_kind: dict[str, Counter] = {}
    totals_by_kind: Counter[str] = Counter()
    all_unmapped: Counter[str] = Counter()

    def kind_of(artifact_type: str | None) -> str:
        token = (artifact_type or "").strip().lower().replace("-", "_").replace(" ", "_")
        if token == "route":
            return "route"
        if token in {"data_service", "dataservice"}:
            return "data_service"
        return "job"

    async with httpx.AsyncClient(
        base_url=base_url, timeout=60.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        artifact_types: dict[str, str] = {}
        offset = 0
        while True:
            page = await client.get(
                "/orchestration/artifacts", params={"limit": 100, "offset": offset}
            )
            page.raise_for_status()
            body = page.json()
            rows = body if isinstance(body, list) else body.get("items", [])
            if not rows:
                break
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    artifact_types[str(row["id"])] = str(row.get("type") or "")
            offset += len(rows)
            if len(rows) < 100:
                break

        # Task ids are tenant-scoped: an id mirrored from another source 404s
        # here. Discovering them from this token's own estate is the only way
        # the probe is safe to point at any source.
        tasks: list[tuple[str, str]] = []
        offset = 0
        while True:
            listing = await client.get(
                "/orchestration/executables/tasks", params={"limit": 100, "offset": offset}
            )
            listing.raise_for_status()
            body = listing.json()
            rows = body if isinstance(body, list) else body.get("items", [])
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                tid = row.get("executable") or row.get("id")
                if not tid:
                    continue
                if task_ids and str(tid) not in task_ids:
                    continue
                tasks.append((str(tid), kind_of(artifact_types.get(str(row.get("artifactId"))))))
            offset += len(rows)
            if len(rows) < 100:
                break

        kind_totals = Counter(kind for _tid, kind in tasks)
        print(f"discovered {len(tasks)} executables: {dict(kind_totals)}")

        for task_id, kind in tasks:
            # Routes/Data Services stay deployed longer than the window, so a
            # windowed call alone reports them as having no activity at all.
            param_sets = [{**window, "limit": 100, "offset": 0}]
            if kind in {"route", "data_service"}:
                param_sets.append({"limit": 100, "offset": 0})
            items: list = []
            seen: set[str] = set()
            for params in param_sets:
                response = await client.get(
                    f"/processing/executables/tasks/{task_id}/executions",
                    # TMC caps this endpoint at 100 ("Parameter `limit` must be
                    # in range [1, 100]"); a larger value is a 400, not a clamp.
                    params=params,
                )
                if response.status_code != 200:
                    print(f"  {kind} {task_id}: HTTP {response.status_code} {response.text[:120]}")
                    continue
                payload = response.json()
                rows = payload if isinstance(payload, list) else payload.get("items", [])
                for row in rows:
                    rid = str(row.get("executionId") or row.get("id") or id(row))
                    if rid not in seen:
                        seen.add(rid)
                        items.append(row)
            if not items:
                continue
            statuses, fields, unmapped = summarize(items, task_id)
            totals_by_kind[kind] += len(items)
            by_kind.setdefault(kind, Counter()).update(statuses)
            fields_by_kind.setdefault(kind, Counter()).update(fields)
            all_unmapped.update(unmapped)

    for kind in ("job", "route", "data_service"):
        total = totals_by_kind[kind]
        if not total:
            print(f"\n=== {kind.upper()}: no executions returned ===")
            continue
        print(f"\n=== {kind.upper()} STATUS VOCABULARY ({total} executions) ===")
        for status, count in by_kind[kind].most_common():
            flag = "" if status.lower() in MAPPED else "   <-- UNMAPPED, stored as 'unknown'"
            print(f"  {status:<32} {count:>5}{flag}")
        print(f"--- {kind} fields (non-null fill) ---")
        for field, count in sorted(fields_by_kind[kind].items(), key=lambda kv: -kv[1]):
            mark = "consumed" if field in CONSUMED else "DROPPED "
            print(f"  [{mark}] {field:<34} {count:>5}/{total}")

    if all_unmapped:
        print("\n=== UNMAPPED STATUS CONTEXT ===")
        for line, count in all_unmapped.most_common(12):
            print(f"  x{count:<4} {line}")


if __name__ == "__main__":
    asyncio.run(main())
