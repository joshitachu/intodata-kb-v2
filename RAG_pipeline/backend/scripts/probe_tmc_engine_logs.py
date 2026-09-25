"""Read-only probe: does EVERY engine in a TMC cluster return a deployment log?

A clustered estate deploys one Route to several remote engines. TMC models that
as ONE task with one execution per engine, so `job_id` does not distinguish
them -- `engine_id` does. The ingest selects deployment logs keyed on
`(source_key, job_id)`, which keeps a single engine's execution and never asks
the others. This probe asks each engine directly.

For every engine it takes the newest Route/Data Service deployments out of the
mirror and calls the same log endpoint the ingest uses, reporting only:

  - the `totalSize` TMC reports for that execution
  - how many `DS RUNNER METRIC - ROUTE` lines the tail carries
  - which route names those lines describe

No log line is ever printed. The engine log is the shared OSGi Runtime log and
is explicitly NOT attribution-safe: it carries order messages, EDIFACT
invoices, SOAP bodies and IBANs. Route names are asset identifiers already
present in the delivered documentation; the counters are what this probe is
for.

Usage (inside the backend container):
    python -m scripts.probe_tmc_engine_logs <source_id> [deployments_per_engine]
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
_TAIL_LINES = 100
_PAGE_SIZE = 200


async def _newest_per_engine(
    session, project_id: str, per_engine: int
) -> dict[str, list[tuple[str, str, str]]]:
    """(run_id, job_name, state) per engine, newest deployment first."""
    stmt = (
        select(
            RuntimeJobRunRow.engine_name,
            RuntimeJobRunRow.engine_id,
            RuntimeJobRunRow.run_id,
            RuntimeJobRunRow.job_name,
            RuntimeJobRunRow.state,
        )
        .where(
            # Filter on project, not source_key: mirrored rows on this estate
            # carry the generic `tmc` key rather than the source's own.
            RuntimeJobRunRow.project_id == project_id,
            RuntimeJobRunRow.executable_kind.in_(_DEPLOYMENT_KINDS),
        )
        .order_by(RuntimeJobRunRow.started_at.desc())
    )
    per_engine_runs: dict[str, list[tuple[str, str, str]]] = {}
    for engine_name, engine_id, run_id, job_name, state in (await session.execute(stmt)).all():
        sleutel = engine_name or engine_id or "(geen engine)"
        bucket = per_engine_runs.setdefault(sleutel, [])
        if len(bucket) < per_engine:
            bucket.append((run_id, job_name or "(naamloos)", state or "?"))
    return per_engine_runs


def _items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw = payload.get("items", payload.get("data", payload.get("logs", [])))
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return []


def _total(payload: object) -> int | None:
    if isinstance(payload, dict):
        for key in ("totalSize", "total"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
    return None


async def _probe_one(client: httpx.AsyncClient, execution_id: str) -> tuple[int, list[str]]:
    """(totalSize TMC reports, route names in the metric lines of the tail)."""
    path = f"/monitoring/executions/{execution_id}/logs"
    head = await client.get(path, params={"count": 1, "startIndex": 0, "order": "ASC"})
    head.raise_for_status()
    body = head.json()
    totaal = _total(body)
    if totaal is None:
        totaal = len(_items(body))
    if totaal <= 0:
        return 0, []

    # Pure tail: the counters are cumulative, so the newest line is the answer.
    start = max(0, totaal - _TAIL_LINES)
    namen: list[str] = []
    while start < totaal:
        page = await client.get(
            path,
            params={"count": min(_PAGE_SIZE, totaal - start), "startIndex": start, "order": "ASC"},
        )
        page.raise_for_status()
        batch = _items(page.json())
        if not batch:
            break
        for item in batch:
            bericht = item.get("logMessage") or item.get("message") or item.get("text") or ""
            gelezen = _route_metrics_from_log(str(bericht))
            if gelezen:
                namen.append(gelezen[0])
        start += len(batch)
    return totaal, namen


async def main() -> None:
    source_id = int(sys.argv[1])
    per_engine = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _key_id = await repo.read_credentials(source_id)
        bron = (
            await session.execute(select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id))
        ).scalar_one()
        per_engine_runs = await _newest_per_engine(session, bron.estate_key, per_engine)

    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token or not base_url:
        raise SystemExit("source has no usable TMC credentials")

    print(f"bron {bron.source_key} / estate {bron.estate_key}")
    print(f"engines in de spiegel: {len(per_engine_runs)}\n")

    async with httpx.AsyncClient(
        base_url=base_url, timeout=60.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        for engine, runs in sorted(per_engine_runs.items()):
            print(f"=== {engine} ===")
            alle_namen: Counter[str] = Counter()
            met_inhoud = 0
            for run_id, job_name, state in runs:
                try:
                    totaal, namen = await _probe_one(client, run_id)
                except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
                    print(f"  {job_name[:48]:<48} {state:<9} FOUT {type(exc).__name__}: {exc}")
                    continue
                if totaal > 0:
                    met_inhoud += 1
                alle_namen.update(namen)
                print(
                    f"  {job_name[:48]:<48} {state:<9} totalSize={totaal:<8} "
                    f"metricregels={len(namen):<3} routes={len(set(namen))}"
                )
            print(
                f"  -> {met_inhoud} van {len(runs)} deployments leverden loginhoud; "
                f"{len(alle_namen)} unieke routes met tellers\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
