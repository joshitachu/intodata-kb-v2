"""CLI: ``python -m evals ...``

Subcommands
-----------
``run``       score a suite against fixtures (default) or a live backend
``validate``  check the suite is well-formed and its evidence files still exist
``cases``     list the suite

Exit codes: 0 = every case passed, 1 = at least one deterministic failure,
2 = the run itself could not be performed. CI can gate on 1 without ever
needing a model key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evals.answers import FixtureProvider, HttpProvider, dump_answers
from evals.cases import (
    default_suite_path,
    load_eval_set,
    load_evidence_aliases,
    resolve_evidence_path,
)
from evals.judge import get_judge
from evals.report import render_json, render_text
from evals.runner import Runner

# Repository root: evals/ -> backend/ -> RAG_pipeline/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "observed_baseline.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="score a suite")
    run.add_argument("--suite", default=str(default_suite_path()))
    run.add_argument(
        "--fixtures",
        default=str(DEFAULT_FIXTURE),
        help="recorded answers to score (offline mode)",
    )
    run.add_argument("--live", action="store_true", help="query a running backend instead")
    run.add_argument("--base-url", default=os.environ.get("EVAL_BASE_URL", "http://localhost:8000"))
    run.add_argument(
        "--access-token",
        default=os.environ.get("EVAL_ACCESS_TOKEN", ""),
        help="JWT access token cookie value (live mode only)",
    )
    run.add_argument("--judge", choices=("none", "heuristic", "claude", "auto"), default="none")
    run.add_argument("--case", action="append", dest="cases", help="run only these case ids")
    run.add_argument("--format", choices=("text", "json"), default="text")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--record", help="write the answers from this run to a fixture file")

    validate = sub.add_parser("validate", help="validate the suite and its evidence paths")
    validate.add_argument("--suite", default=str(default_suite_path()))

    cases = sub.add_parser("cases", help="list the suite")
    cases.add_argument("--suite", default=str(default_suite_path()))

    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    eval_set = load_eval_set(args.suite)
    aliases = load_evidence_aliases()
    problems: list[str] = []
    for case in eval_set.cases:
        if not case.evidence:
            problems.append(f"{case.id}: no evidence declared")
        for ref in case.evidence:
            if not resolve_evidence_path(ref, REPO_ROOT, aliases).exists():
                problems.append(f"{case.id}: missing evidence file {ref.path}")
        # An unanswerable case must point at evidence that is out of corpus,
        # otherwise it is mislabelled and will teach the system the wrong lesson.
        if not case.answerable and all(ref.in_corpus for ref in case.evidence):
            problems.append(
                f"{case.id}: marked unanswerable but every evidence ref is in_corpus=true"
            )

    print(f"suite: {eval_set.name} v{eval_set.version} -- {len(eval_set.cases)} cases")
    print(
        f"  answerable: {len(eval_set.answerable_cases)}  "
        f"unanswerable: {len(eval_set.unanswerable_cases)}"
    )
    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("  all evidence paths resolve; suite is well-formed")
    return 0


def _cmd_cases(args: argparse.Namespace) -> int:
    eval_set = load_eval_set(args.suite)
    for case in eval_set.cases:
        kind = "answerable  " if case.answerable else "UNANSWERABLE"
        print(f"{case.id:<40s} {kind} {case.answer_type:<14s} {case.project}")
        print(f"    {case.question}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    eval_set = load_eval_set(args.suite)

    if args.live:
        if not args.access_token:
            print(
                "live mode needs --access-token (or EVAL_ACCESS_TOKEN): "
                "/api/v1/query requires a JWT cookie",
                file=sys.stderr,
            )
            return 2
        provider = HttpProvider(base_url=args.base_url, access_token=args.access_token)
        throttle = 2.1  # /api/v1/query is limited to 30/min
    else:
        provider = FixtureProvider.from_file(args.fixtures)
        throttle = 0.0

    judge = None if args.judge == "none" else get_judge(args.judge)

    runner = Runner(
        eval_set=eval_set,
        provider=provider,
        judge=judge,
        throttle_s=throttle,
        progress=(lambda msg: print(msg, file=sys.stderr)) if args.verbose else None,
    )
    result = runner.run(case_ids=args.cases)

    if args.record:
        dump_answers(
            runner.collected_answers,
            args.record,
            name=f"{eval_set.name}-{provider.name}",
            note="recorded by `python -m evals run --record`",
        )

    print(render_json(result) if args.format == "json" else render_text(result, verbose=args.verbose))
    return 0 if not result.failing else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "cases":
        return _cmd_cases(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
