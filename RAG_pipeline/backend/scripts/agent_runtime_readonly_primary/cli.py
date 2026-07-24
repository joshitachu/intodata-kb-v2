"""Run the Slice 3 read-only primary evaluation reports."""

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
    parser.add_argument("--variants", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    args = parser.parse_args()

    if args.authenticated:
        report = asyncio.run(run_authenticated(args.output_dir, variants=args.variants))
        print(
            "Slice 3 authenticated read-only verdict: "
            f"{report['verdict']} (boundary {report['boundary']['rate']:.2%}, "
            f"tool {report['read_only_tool']['rate']:.2%}, "
            f"{report['provider_requests']} provider requests)"
        )
        return 0 if report["verdict"] == "GO" else 2

    reports = asyncio.run(run_deterministic(args.output_dir))
    verdict = (
        "PASS"
        if reports["grounding"]["verdict"] == "PASS"
        and reports["parity"]["verdict"] == "PASS"
        else "FAIL"
    )
    print(f"Slice 3 deterministic verdict: {verdict}")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
