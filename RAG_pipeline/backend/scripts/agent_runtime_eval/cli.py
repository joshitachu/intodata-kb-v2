"""One-command Slice 0 corpus lint, schema export, and legacy baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .corpus import CORPUS_PATH, FIXTURE_ROOT, lint_corpus, load_corpus
from .models import EvaluationReport, RunTrace, Scenario
from .runner import RecordedTraceTarget, evaluate_target, render_markdown


DEFAULT_TRACES = FIXTURE_ROOT / "legacy_runtime_traces_v1.json"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[1] / "eval_reports" / "agent_runtime_v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--schema-dir", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    scenarios = load_corpus(args.corpus)
    errors = lint_corpus(scenarios)
    if errors:
        for error in errors:
            print(f"LINT FAIL: {error}")
        return 2
    target = RecordedTraceTarget(args.traces)
    report = evaluate_target(scenarios, target)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "current_runtime_baseline_v1.json"
    markdown_path = args.output_dir / "current_runtime_baseline_v1.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n")
    markdown_path.write_text(render_markdown(report))
    if args.schema_dir:
        args.schema_dir.mkdir(parents=True, exist_ok=True)
        schemas = {
            "scenario_schema_v1.json": Scenario.model_json_schema(),
            "run_trace_schema_v1.json": RunTrace.model_json_schema(),
            "report_schema_v1.json": EvaluationReport.model_json_schema(),
        }
        for filename, schema in schemas.items():
            (args.schema_dir / filename).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(
        f"PASS lint; {report.summary.total} scenarios; {report.summary.evaluated} captured; "
        f"{report.summary.product_failed} documented product failures; "
        f"{report.summary.not_evaluated} evaluator gaps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
