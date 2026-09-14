"""Measure and capture the runtime router's slow endpoints.

Run INSIDE the backend container:

    docker exec intodata-kb-v2-backend-1 python scripts/bench_runtime_endpoints.py \
        --label before --out /tmp/bench

Writes `<out>/<label>.json` (per-endpoint response bodies) plus a latency table on
stdout. Auth is cookie based, using a freshly minted superadmin access token, so the
measurement path is the real ASGI stack including auth and scope resolution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import statistics
import time
import urllib.error
import urllib.request

ENDPOINTS: list[tuple[str, str]] = [
    ("overview", "/api/v1/runtime/overview?project_id={project}"),
    ("incidents", "/api/v1/runtime/incidents?project_id={project}&limit=50"),
    ("health", "/api/v1/runtime/health?project_id={project}"),
    ("job-reliability", "/api/v1/runtime/job-reliability?project_id={project}"),
    ("engine-health", "/api/v1/runtime/engine-health?project_id={project}"),
    ("job-health", "/api/v1/runtime/job-health?project_id={project}"),
    ("plan-health", "/api/v1/runtime/plan-health?project_id={project}"),
    ("overview-unscoped", "/api/v1/runtime/overview"),
    ("health-unscoped", "/api/v1/runtime/health"),
    ("incidents-unscoped", "/api/v1/runtime/incidents?limit=50"),
    ("incidents-offset", "/api/v1/runtime/incidents?project_id={project}&limit=25&offset=10"),
]


async def _token() -> str:
    from sqlalchemy import select

    from src.auth.security import create_access_token
    from src.db.models import User
    from src.db.session import async_session_factory

    async with async_session_factory() as db:
        user = (
            await db.execute(select(User).where(User.is_superadmin == True).limit(1))  # noqa: E712
        ).scalar_one()
        return create_access_token(user.id, user.username, True)


def _call(base: str, path: str, cookie: str) -> tuple[float, int, bytes]:
    req = urllib.request.Request(base + path, headers={"Cookie": f"access_token={cookie}"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:  # keep the body so failures are diagnosable
        body = exc.read()
        status = exc.code
    return (time.perf_counter() - start) * 1000.0, status, body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default="/tmp/bench")
    parser.add_argument("--project", default="alitalend")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    cookie = asyncio.run(_token())
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    bodies: dict[str, object] = {}
    rows: list[tuple[str, int, float, float, int]] = []
    for name, template in ENDPOINTS:
        path = template.format(project=args.project)
        samples: list[float] = []
        status = 0
        body = b""
        for _ in range(args.repeats):
            elapsed, status, body = _call(args.base, path, cookie)
            samples.append(elapsed)
        bodies[name] = {
            "path": path,
            "status": status,
            "bytes": len(body),
            "body": json.loads(body) if body else None,
        }
        rows.append(
            (name, status, statistics.median(samples), min(samples), len(body))
        )

    (out_dir / f"{args.label}.json").write_text(
        json.dumps(bodies, indent=2, sort_keys=True, default=str)
    )

    print(f"{'endpoint':22} {'st':>4} {'median ms':>10} {'min ms':>9} {'bytes':>8}")
    for name, status, median, lo, size in rows:
        print(f"{name:22} {status:>4} {median:>10.1f} {lo:>9.1f} {size:>8}")


if __name__ == "__main__":
    main()
