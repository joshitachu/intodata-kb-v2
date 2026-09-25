"""Read-only diagnosis: why does one engine in a TMC cluster yield no logs?

Measured on the live mirror: of the route/data-service deployments, engine
PRD_RE_01 returned 420.565 log lines over 25 of 30 deployments while PRD_RE_02
returned zero over all 72. This probe asks TMC directly, per engine, which of
three mutually exclusive explanations holds:

  A. TMC reports totalSize == 0        -> the engine never shipped the log
  B. totalSize > 0 but our read is empty -> our paginated read is wrong
  C. the request errors                -> permission/route/parameter problem

It also tries the ASYNC export lane (logs/status -> downloadUrl) that the
paginated lane falls back from, because the two are served differently.

NO log line is ever printed. The OSGi runtime log is not attribution-safe and
this estate carries dispensing data: only counts, HTTP codes, and route names
recovered from `DS RUNNER METRIC` counter lines are emitted.

Usage (inside the backend container):
    python -m scripts.probe_tmc_engine_log_gap <source_id> [per_engine]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

import httpx
from sqlalchemy import select

from src.db.models.runtime_data import RuntimeJobRunRow, RuntimeSourceRow
from src.db.session import async_session_factory
from src.runtime.providers.talend_tmc import _route_metrics_from_log
from src.runtime.store.repository import RuntimeStoreRepository

_DEPLOYMENT_KINDS = ("route", "data_service")
_TAIL = 100
_PAGE = 200


async def _newest_per_engine(session, estate_key, per_engine):
    stmt = (
        select(
            RuntimeJobRunRow.engine_name,
            RuntimeJobRunRow.engine_id,
            RuntimeJobRunRow.run_id,
            RuntimeJobRunRow.job_name,
            RuntimeJobRunRow.state,
        )
        .where(
            RuntimeJobRunRow.project_id == estate_key,
            RuntimeJobRunRow.executable_kind.in_(_DEPLOYMENT_KINDS),
        )
        .order_by(RuntimeJobRunRow.started_at.desc())
    )
    out: dict[str, list[tuple[str, str, str]]] = {}
    for engine_name, engine_id, run_id, job_name, state in (await session.execute(stmt)).all():
        key = engine_name or engine_id or "(no engine)"
        bucket = out.setdefault(key, [])
        if len(bucket) < per_engine:
            bucket.append((run_id, job_name or "(unnamed)", state or "?"))
    return out


def _items(payload):
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        raw = payload.get("items", payload.get("data", payload.get("logs", [])))
        return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
    return []


def _total(payload):
    if isinstance(payload, dict):
        for key in ("totalSize", "total", "totalElements"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
    return None


async def _paginated(client, execution_id):
    """(verdict, totalSize, lines_read, route names in metric lines)."""
    path = f"/monitoring/executions/{execution_id}/logs"
    try:
        head = await client.get(path, params={"count": 1, "startIndex": 0, "order": "ASC"})
    except Exception as exc:  # noqa: BLE001 - a probe reports
        return f"C:{type(exc).__name__}", -1, 0, []
    if head.status_code >= 400:
        return f"C:HTTP{head.status_code}", -1, 0, []
    body = head.json()
    total = _total(body)
    if total is None:
        total = len(_items(body))
    if total <= 0:
        return "A:empty", 0, 0, []

    start, read, names = max(0, total - _TAIL), 0, []
    while start < total:
        page = await client.get(
            path,
            params={"count": min(_PAGE, total - start), "startIndex": start, "order": "ASC"},
        )
        if page.status_code >= 400:
            return f"C:HTTP{page.status_code}@{start}", total, read, names
        batch = _items(page.json())
        if not batch:
            break
        read += len(batch)
        for item in batch:
            msg = item.get("logMessage") or item.get("message") or item.get("text") or ""
            got = _route_metrics_from_log(str(msg))
            if got:
                names.append(got[0])
        start += len(batch)
    verdict = "OK" if read else "B:total>0_but_no_rows"
    return verdict, total, read, names


async def _async_export(client, execution_id):
    """Try the export lane. Returns a short verdict string; downloads nothing."""
    try:
        started = await client.get(f"/monitoring/executions/{execution_id}/logs/status")
    except Exception as exc:  # noqa: BLE001
        return f"status_err:{type(exc).__name__}"
    if started.status_code >= 400:
        return f"status_HTTP{started.status_code}"
    try:
        body = started.json()
    except Exception:  # noqa: BLE001
        return "status_not_json"
    if not isinstance(body, dict):
        return f"status_{type(body).__name__}"
    url = body.get("downloadUrl") or body.get("url")
    state = body.get("status") or body.get("state")
    return f"status={state!r} downloadUrl={'yes' if url else 'no'}"


async def main() -> None:
    source_id = int(sys.argv[1])
    per_engine = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _key_id = await repo.read_credentials(source_id)
        source = (
            await session.execute(select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id))
        ).scalar_one()
        estate_key = source.estate_key
        per_engine_runs = await _newest_per_engine(session, estate_key, per_engine)

    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token or not base_url:
        raise SystemExit("source has no usable TMC credentials")

    print(f"source {source.source_key} / estate {estate_key} / base {base_url}")
    print(f"engines with deployments in the mirror: {len(per_engine_runs)}\n")

    async with httpx.AsyncClient(
        base_url=base_url, timeout=90.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        for engine, runs in sorted(per_engine_runs.items()):
            print(f"=== {engine} ===")
            names: Counter[str] = Counter()
            with_content = 0
            for run_id, job_name, state in runs:
                verdict, total, read, got = await _paginated(client, run_id)
                if read:
                    with_content += 1
                names.update(got)
                export = await _async_export(client, run_id)
                print(
                    f"  {job_name[:44]:<44} {state:<8} {verdict:<22} "
                    f"total={total:<8} read={read:<5} metric_lines={len(got):<4} | export: {export}"
                )
            print(
                f"  -> {with_content} of {len(runs)} deployments returned content; "
                f"{len(names)} distinct routes carried counters\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
