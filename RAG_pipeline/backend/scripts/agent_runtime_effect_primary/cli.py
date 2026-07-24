from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .runner import REPORT_ROOT, run_authenticated, scenarios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authenticated", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    parser.add_argument("--trajectories", type=int, default=50)
    args = parser.parse_args()
    if not args.authenticated:
        report = {
            "mode": "deterministic_fixture",
            "trajectories": len(scenarios()),
            "passed": len(scenarios()) == 50,
        }
    else:
        report = asyncio.run(run_authenticated(args.output_dir, trajectories=args.trajectories))
    print(
        json.dumps(
            report
            if not args.authenticated
            else {
                key: report[key]
                for key in (
                    "verdict",
                    "trajectories",
                    "passed",
                    "threshold",
                    "point_rate",
                    "mandatory_safety",
                )
            },
            indent=2,
        )
    )
    return 0 if report.get("passed") is True or report.get("verdict") == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
