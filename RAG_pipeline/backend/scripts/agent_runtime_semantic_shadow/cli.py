"""Run the disabled Slice 2 semantic shadow reports."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .runner import REPORT_ROOT, run_authenticated, run_deterministic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deterministic", action="store_true")
    mode.add_argument("--authenticated", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    args = parser.parse_args()
    if args.authenticated:
        report = asyncio.run(run_authenticated(args.output_dir))
        print(f"Slice 2 authenticated semantic-selection verdict: {report['verdict']}")
        return 0 if report["verdict"] == "GO" else 2
    reports = asyncio.run(run_deterministic(args.output_dir))
    verdict = (
        "PASS"
        if reports["parity"]["verdict"] == "PASS"
        and reports["safety"]["verdict"] == "PASS"
        else "FAIL"
    )
    print(f"Slice 2 deterministic verdict: {verdict}")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
