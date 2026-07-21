"""Run the disabled Slice 2 semantic shadow reports."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .runner import (
    REPORT_ROOT,
    run_authenticated,
    run_authenticated_full_v2,
    run_authenticated_scenario_matrix_v2,
    run_authenticated_v2,
    run_deterministic,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--deterministic", action="store_true")
    mode.add_argument("--authenticated", action="store_true")
    mode.add_argument("--authenticated-v2", action="store_true")
    mode.add_argument("--authenticated-full-v2", action="store_true")
    mode.add_argument("--authenticated-draft-v2", action="store_true")
    mode.add_argument("--authenticated-action-v2", action="store_true")
    mode.add_argument("--authenticated-action-recovery-v2", action="store_true")
    mode.add_argument("--authenticated-no-action-v2", action="store_true")
    mode.add_argument("--authenticated-no-action-recovery-v2", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    args = parser.parse_args()
    matrix = (
        "draft_retest"
        if args.authenticated_draft_v2
        else "action_recovery"
        if args.authenticated_action_recovery_v2
        else "action"
        if args.authenticated_action_v2
        else "no_action"
        if args.authenticated_no_action_v2
        else "no_action_recovery"
        if args.authenticated_no_action_recovery_v2
        else None
    )
    if matrix is not None:
        report = asyncio.run(run_authenticated_scenario_matrix_v2(matrix, args.output_dir))
        print(
            f"Slice 2 corrected {matrix} verdict: {report['verdict']} "
            f"({report['passed']}/{report['total']})"
        )
        return 0 if report["verdict"] == "PASS" else 2
    if args.authenticated_full_v2:
        report = asyncio.run(run_authenticated_full_v2(args.output_dir))
        print(
            "Slice 2 full corrected semantic-selection verdict: "
            f"{report['verdict']} ({report['provider_requests']} provider requests)"
        )
        return 0 if report["verdict"] == "GO" else 2
    if args.authenticated_v2:
        report = asyncio.run(run_authenticated_v2(args.output_dir))
        print(
            "Slice 2 corrected semantic diagnostic verdict: "
            f"{report['verdict']} (release gate eligible: "
            f"{report['release_gate_eligible']})"
        )
        return 0 if report["verdict"] == "PASS" else 2
    if args.authenticated:
        report = asyncio.run(run_authenticated(args.output_dir))
        print(f"Slice 2 authenticated semantic-selection verdict: {report['verdict']}")
        return 0 if report["verdict"] == "GO" else 2
    reports = asyncio.run(run_deterministic(args.output_dir))
    verdict = (
        "PASS"
        if reports["parity"]["verdict"] == "PASS" and reports["safety"]["verdict"] == "PASS"
        else "FAIL"
    )
    print(f"Slice 2 deterministic verdict: {verdict}")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
