"""Run the Slice 4 plan-primary acceptance harness without writing domain state."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.harness.agent_plans import AgentPlanStepInput, topology_requires_plan
from .runner import REPORT_ROOT, run_authenticated

_FIXTURE = Path(__file__).parents[1] / "eval_fixtures/agent_runtime_v1/plan_primary_v1.json"


def deterministic_report() -> dict:
    cases = json.loads(_FIXTURE.read_text())["cases"]
    outcomes = []
    for item in cases:
        steps = [AgentPlanStepInput.model_validate(step) for step in item["steps"]]
        outcomes.append({"id": item["id"], "expected": item["plan_required"], "actual": topology_requires_plan(steps)})
    passed = all(item["expected"] == item["actual"] for item in outcomes)
    return {"mode": "deterministic", "passed": passed, "effects": 0, "outcomes": outcomes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--authenticated", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    parser.add_argument("--trajectories", type=int, default=50)
    args = parser.parse_args()
    if args.authenticated:
        report = asyncio.run(run_authenticated(args.output_dir, trajectories=args.trajectories))
        print(json.dumps({key: report[key] for key in ("verdict", "trajectories", "passed", "threshold", "point_rate", "effects")}, indent=2))
        return 0 if report["verdict"] == "GO" else 2
    report = deterministic_report()
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
