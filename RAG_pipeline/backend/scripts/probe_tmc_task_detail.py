"""Read-only probe: does a standard Job's task detail carry a version, and does
a Route deployment actually return log lines?

Two questions the mirror cannot answer on its own, because the provider never
asks TMC either one for a standard Job:

1. ``enrich_deployable_task`` fetches ``/orchestration/executables/tasks/{id}``
   only for ROUTE and DATA_SERVICE artifacts, so ``artifact_version`` is empty
   for every Job in the mirror. That empty column has been read as "TMC does not
   expose it"; it only proves we never asked.
2. Route executions carry no log lines in the mirror at all (0 of 100), while
   the sync does attempt a deployment log fetch for the latest run per route.
   Either TMC returns nothing for these, or it returns something we drop.

Prints FIELD NAMES and COUNTS only -- never a token, never a value, never a log
line. Nothing is written anywhere.

Usage (inside the backend container):
    python -m scripts.probe_tmc_task_detail <source_id> [max_per_kind]
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

import httpx

from src.db.session import async_session_factory
from src.runtime.store.repository import RuntimeStoreRepository

_TIMEOUT = httpx.Timeout(30.0)
_VERSIEACHTIG = ("version", "artifactversion", "buildversion", "revision", "build")


def _plat(obj: object, voorvoegsel: str = "") -> list[str]:
    """Veldnamen, niet de waarden. Een waarde kan klantgegeven zijn."""
    uit: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            pad = f"{voorvoegsel}{k}"
            uit.append(pad)
            if isinstance(v, (dict, list)):
                uit.extend(_plat(v, pad + "."))
    elif isinstance(obj, list) and obj:
        uit.extend(_plat(obj[0], voorvoegsel))
    return uit


async def main() -> None:
    source_id = int(sys.argv[1])
    per_soort = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    async with async_session_factory() as session:
        repo = RuntimeStoreRepository(session)
        credentials, _key_id = await repo.read_credentials(source_id)
    token = str(credentials.get("api_token") or "").strip()
    base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
    if not token or not base_url:
        raise SystemExit("bron heeft geen bruikbare TMC-gegevens")
    kop = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=kop) as client:

        async def haal(pad: str, **kw: object) -> object:
            r = await client.get(f"{base_url}{pad}", **kw)  # type: ignore[arg-type]
            r.raise_for_status()
            return r.json()

        artefacten = (await haal("/orchestration/artifacts", params={"limit": 100})) or {}
        rijen = artefacten.get("items", artefacten) if isinstance(artefacten, dict) else artefacten
        soort_van = {a.get("id"): (a.get("type") or "").lower() for a in rijen if isinstance(a, dict)}
        print(f"artefacten opgehaald: {len(soort_van)}")
        print("  soorten:", dict(Counter(soort_van.values())))
        heeft_versie = sum(1 for a in rijen if isinstance(a, dict) and a.get("version"))
        print(f"  daarvan met een 'version' in de LIJST: {heeft_versie}")

        taken = (await haal("/orchestration/executables/tasks", params={"limit": 100})) or {}
        trijen = taken.get("items", taken) if isinstance(taken, dict) else taken
        print(f"taken opgehaald: {len(trijen)}")

        gekozen: dict[str, list[dict]] = {}
        for t in trijen:
            if not isinstance(t, dict):
                continue
            soort = soort_van.get(t.get("artifactId") or "", "onbekend")
            gekozen.setdefault(soort, [])
            if len(gekozen[soort]) < per_soort:
                gekozen[soort].append(t)

        print()
        print("=== VRAAG 1: draagt het taakdetail een versie, per soort?")
        for soort, items in sorted(gekozen.items()):
            for t in items:
                tid = ((t.get("executable") or {}) if isinstance(t.get("executable"), dict)
                       else {}).get("id") or t.get("executable") or t.get("id")
                if not tid:
                    continue
                try:
                    detail = await haal(f"/orchestration/executables/tasks/{tid}")
                except httpx.HTTPStatusError as exc:
                    print(f"  {soort:<14} HTTP {exc.response.status_code}")
                    continue
                velden = _plat(detail)
                versie = [v for v in velden if any(k in v.lower() for k in _VERSIEACHTIG)]
                art = detail.get("artifact") if isinstance(detail, dict) else None
                gevuld = bool(isinstance(art, dict) and str(art.get("version") or "").strip())
                print(f"  {soort:<14} velden={len(velden):<4} versieachtig={versie or '-'}")
                print(f"  {'':<14} artifact.version gevuld: {gevuld}")

        print()
        print("=== VRAAG 2: leveren route-uitvoeringen logregels?")
        for soort in ("route", "standard"):
            for t in gekozen.get(soort, [])[:1]:
                tid = ((t.get("executable") or {}) if isinstance(t.get("executable"), dict)
                       else {}).get("id") or t.get("executable") or t.get("id")
                try:
                    hist = await haal(f"/processing/executables/tasks/{tid}/executions",
                                      params={"limit": 5})
                except httpx.HTTPStatusError as exc:
                    print(f"  {soort:<10} historie HTTP {exc.response.status_code}")
                    continue
                items = hist.get("items", hist) if isinstance(hist, dict) else hist
                print(f"  {soort:<10} uitvoeringen in de historie: {len(items)}")
                for uit in items[:2]:
                    eid = uit.get("executionId") or uit.get("id") if isinstance(uit, dict) else None
                    status = uit.get("status") if isinstance(uit, dict) else "?"
                    if not eid:
                        continue
                    try:
                        r = await client.post(f"{base_url}/monitoring/executions/{eid}/logs",
                                              json={"limit": 50})
                        code, regels = r.status_code, None
                        if r.status_code < 400:
                            body = r.json()
                            lijst = body.get("items", body) if isinstance(body, dict) else body
                            regels = len(lijst) if isinstance(lijst, list) else "geen lijst"
                    except Exception as exc:  # noqa: BLE001
                        code, regels = type(exc).__name__, None
                    print(f"  {'':<10}   status={status:<12} logs HTTP {code} -> regels: {regels}")


if __name__ == "__main__":
    asyncio.run(main())
