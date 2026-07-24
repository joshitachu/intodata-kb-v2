"""Evaluate a completed authenticated Slice 7 evidence checkpoint."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from scripts.agent_runtime_eval.corpus import load_corpus, real_project_snapshot_path

from .capture import (
    PublicApiCollector,
    bounded_selection,
    collection_batches,
    load_checkpoint,
    validate_published_project_inventory,
)
from .evidence import assemble_release_evidence
from .gate import evaluate_release
from .models import CaptureReview, ReleaseEvidence
from .review import review_captures


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("evidence", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    collect = subparsers.add_parser("collect")
    collect.add_argument("corpus", type=Path)
    collect.add_argument("--checkpoint", type=Path, required=True)
    collect.add_argument("--base-url", default="http://127.0.0.1:8000")
    collect.add_argument("--docs-base-url", default="http://127.0.0.1:8002/mcp")
    collect.add_argument("--gate-profile", choices=("slice7-bounded-50-v1", "production-1000-v1"), default="slice7-bounded-50-v1")
    collect.add_argument("--max-attempts", type=int, choices=range(1, 51))
    collect.add_argument(
        "--scenario-id",
        action="append",
        default=[],
        help="Collect only selected scenario ids (repeatable) before spending on the full gate.",
    )
    review = subparsers.add_parser("review")
    review.add_argument("checkpoint", type=Path)
    review.add_argument("corpus", type=Path)
    review.add_argument("--output", type=Path, required=True)
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("review", type=Path)
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--corpus-version", required=True)
    assemble.add_argument("--application-revision", required=True)
    assemble.add_argument("--runtime-version", required=True)
    assemble.add_argument("--model-profile", required=True)
    assemble.add_argument("--prompt-version", required=True)
    assemble.add_argument("--manifest-sha256", required=True)
    assemble.add_argument("--corpus-sha256", required=True)
    assemble.add_argument(
        "--checkpoint-serialization-review", choices=("pass", "fail", "missing"), required=True,
    )
    assemble.add_argument("--lockfile-sha256", required=True)
    assemble.add_argument("--sbom-sha256", required=True)
    assemble.add_argument("--scanner", required=True)
    assemble.add_argument("--release-owner")
    assemble.add_argument("--release-owner-decision", choices=("accept", "reject"))
    args = parser.parse_args()
    if args.command == "collect":
        token = os.environ.get("NEXUS_EVAL_ACCESS_TOKEN", "")
        readonly_token = os.environ.get("NEXUS_EVAL_READONLY_ACCESS_TOKEN", "")
        if args.gate_profile == "slice7-bounded-50-v1" and not readonly_token:
            raise ValueError(
                "NEXUS_EVAL_READONLY_ACCESS_TOKEN is required for genuine denial scenarios"
            )
        snapshot = real_project_snapshot_path(args.corpus)
        if snapshot is not None:
            validate_published_project_inventory(
                snapshot=snapshot,
                docs_base_url=args.docs_base_url,
                access_token=token,
            )
        collector = PublicApiCollector(
            base_url=args.base_url,
            access_token=token,
            access_tokens_by_family={
                family: readonly_token for family in ("policy_denial", "scope_denied")
                if readonly_token
            },
        )
        captures = load_checkpoint(args.checkpoint)
        try:
            scenarios = load_corpus(args.corpus)
            if args.gate_profile == "slice7-bounded-50-v1":
                selection = bounded_selection(scenarios)
                if args.scenario_id:
                    selected_ids = set(args.scenario_id)
                    selection = [
                        item for item in selection if item[0].scenario_id in selected_ids
                    ]
                    missing_ids = selected_ids.difference(
                        item[0].scenario_id for item in selection
                    )
                    if missing_ids:
                        raise ValueError(
                            f"scenario ids are not in the bounded gate: {sorted(missing_ids)!r}"
                        )
                if args.max_attempts is not None:
                    selection = selection[: args.max_attempts]
                for scenario, selected_transports, family in collection_batches(selection):
                    collector.capture(
                        scenario, checkpoint=args.checkpoint, existing=captures,
                        selected_transports=selected_transports, evaluation_family=family,
                    )
            else:
                for scenario in scenarios:
                    collector.capture(scenario, checkpoint=args.checkpoint, existing=captures)
        finally:
            collector.close()
        print(f"CAPTURED: {len(captures)} authenticated public-API attempts")
        return 0
    if args.command == "review":
        captures = load_checkpoint(args.checkpoint)
        result = review_captures(captures, load_corpus(args.corpus))
        _atomic_write(args.output, result.model_dump_json(indent=2) + "\n")
        print(
            f"REVIEWED: {result.total_attempts} attempts; "
            f"{result.passed_attempts} pass; {result.failed_attempts} fail"
        )
        return 0 if result.failed_attempts == 0 else 2
    if args.command == "assemble":
        review_result = CaptureReview.model_validate_json(args.review.read_text())
        evidence = assemble_release_evidence(
            review_result,
            corpus_version=args.corpus_version,
            application_revision=args.application_revision,
            runtime_version=args.runtime_version,
            model_profile=args.model_profile,
            prompt_version=args.prompt_version,
            manifest_sha256=args.manifest_sha256,
            corpus_sha256=args.corpus_sha256,
            checkpoint_serialization_review=args.checkpoint_serialization_review,
            lockfile_sha256=args.lockfile_sha256,
            sbom_sha256=args.sbom_sha256,
            scanner=args.scanner,
            release_owner=args.release_owner,
            release_owner_decision=args.release_owner_decision,
        )
        _atomic_write(args.output, evidence.model_dump_json(indent=2) + "\n")
        print(f"ASSEMBLED: {len(evidence.attempts)} authenticated release-evidence attempts")
        return 0
    evidence = ReleaseEvidence.model_validate_json(args.evidence.read_text())
    verdict = evaluate_release(evidence)
    _atomic_write(args.output, verdict.model_dump_json(indent=2) + "\n")
    print(f"{verdict.verdict}: {verdict.total_attempts} attempts; {len(verdict.blockers)} blocker(s)")
    return 0 if verdict.verdict == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
