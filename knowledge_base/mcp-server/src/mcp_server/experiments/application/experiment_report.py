"""Sanitized deterministic reports for aggregate experiment evaluation."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
import json
from typing import Any

from mcp_server.experiments.application.evaluate_experiment import ExperimentEvaluationResult


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {key: _json_safe(value[key]) for key in sorted(value)}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    return value


def canonical_experiment_payload(result: ExperimentEvaluationResult) -> dict[str, Any]:
    """Return durable observations only; raw prompts, docs, and envelopes are absent by type."""
    return _json_safe(result)


def render_canonical_json_report(result: ExperimentEvaluationResult) -> str:
    """Render compact canonical JSON suitable for hash-addressed experiment artifacts."""
    return json.dumps(
        canonical_experiment_payload(result), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) + "\n"


def _display(value: object | None) -> str:
    return "unknown" if value is None else str(value)


def _timing_lines(result: ExperimentEvaluationResult) -> list[str]:
    baseline = result.baseline_timing
    candidate = result.candidate_timing
    fields = (
        ("Experiment wall (ms)", "experiment_wall_ms"),
        ("Generation wall (ms)", "generation_wall_ms"),
        ("Queue (ms)", "queue_ms"),
        ("Snapshot (ms)", "snapshot_ms"),
        ("Validation (ms)", "validation_ms"),
        ("Provider latency sum (ms)", "provider_latency_sum_ms"),
        ("Provider latency p50 (ms)", "provider_latency_p50_ms"),
        ("Provider latency p95 (ms)", "provider_latency_p95_ms"),
        ("DAG critical path (ms)", "critical_path_ms"),
    )
    return [
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        *[
            f"| {label} | {_display(getattr(baseline, field))} | {_display(getattr(candidate, field))} |"
            for label, field in fields
        ],
    ]


def _cost_usage_lines(result: ExperimentEvaluationResult) -> list[str]:
    baseline_usage = result.baseline_usage
    candidate_usage = result.candidate_usage
    fields = (
        ("Input tokens", "input_tokens"),
        ("Output tokens", "output_tokens"),
        ("Cache-read tokens", "cache_read_tokens"),
        ("Cache-write tokens", "cache_write_tokens"),
        ("Provider calls", "provider_calls"),
        ("Tool calls", "tool_calls"),
        ("Tool bytes", "tool_bytes"),
        ("Retries", "retries"),
        ("Repairs", "repairs"),
    )
    lines = [
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        *[
            f"| {label} | {_display(getattr(baseline_usage, field))} | {_display(getattr(candidate_usage, field))} |"
            for label, field in fields
        ],
        f"| Provider-reported cost (USD) | {_display(result.baseline_cost.billed_cost_usd)} | {_display(result.candidate_cost.billed_cost_usd)} |",
        f"| Normalized cost (USD) | {_display(result.baseline_cost.normalized_cost_usd)} | {_display(result.candidate_cost.normalized_cost_usd)} |",
    ]
    return lines


def _provenance_lines(result: ExperimentEvaluationResult) -> list[str]:
    baseline = result.baseline_provenance
    candidate = result.candidate_provenance
    fields = (
        ("Citations", "citation_count"),
        ("Resolved citations", "resolved_citation_count"),
        ("Evidence items", "evidence_item_count"),
        ("Cited evidence items", "cited_evidence_item_count"),
        ("Unsupported claims", "unsupported_claim_count"),
        ("Extracted facts", "extracted_fact_count"),
        ("Matched extracted facts", "matched_extracted_fact_count"),
    )
    return [
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        *[
            f"| {label} | {_display(getattr(baseline, field))} | {_display(getattr(candidate, field))} |"
            for label, field in fields
        ],
    ]


def render_markdown_report(result: ExperimentEvaluationResult) -> str:
    """Render an informational-only report without document body or runtime secrets."""
    lines = [
        "# Agentic Experiment Evaluation",
        "",
        "Informational only. This report cannot publish or promote candidate documentation.",
        "",
        "## Evaluator Receipt",
        "",
        f"- Evaluator: `{result.receipt.evaluator_id}` v{result.receipt.evaluator_version}",
        f"- Code SHA-256: `{result.receipt.code_sha256}`",
        f"- Configuration SHA-256: `{result.receipt.configuration_sha256}`",
        "",
        "## Required Document Alignment",
        "",
        "| Document | Status | Semantic inputs | Output contract |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        "| {id} | {alignment} | {semantic} | baseline: {baseline_contract}; candidate: {candidate_contract} |".format(
            id=document.document_id,
            alignment=next(pair.status for pair in result.alignment.pairs if pair.document_id == document.document_id),
            semantic=document.parity.comparability.semantic_status,
            baseline_contract=document.parity.baseline_contract.status,
            candidate_contract=document.parity.candidate_contract.status,
        )
        for document in result.documents
    )
    lines.extend(
        [
            "",
            "## Attempt Inclusion",
            "",
            "| Branch | Total | Accepted | Failed | Retried | Cancelled | Skipped | Output-claim gate |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            "| Baseline | {total} | {accepted} | {failed} | {retried} | {cancelled} | {skipped} | {claims} |".format(
                **asdict(result.baseline_attempts), claims=result.baseline_output_claims.status
            ),
            "| Candidate | {total} | {accepted} | {failed} | {retried} | {cancelled} | {skipped} | {claims} |".format(
                **asdict(result.candidate_attempts), claims=result.candidate_output_claims.status
            ),
            "",
            "## Timing",
            "",
            *_timing_lines(result),
            "",
            "## Usage and Cost",
            "",
            *_cost_usage_lines(result),
            "",
            "## Evidence, Provenance, and Citations",
            "",
            *_provenance_lines(result),
            "",
            "## Verdict",
            "",
            f"Semantic parity confirmed: **{'yes' if result.comparability.semantic_parity_confirmed else 'no'}**",
            f"Candidate verdict: **{result.verdict.candidate_verdict}**",
            "",
            "### Dimension Results",
            "",
        ]
    )
    lines.extend(
        f"- {comparison.dimension}: **{comparison.verdict}**"
        + (f" ({'; '.join(comparison.limitations)})" if comparison.limitations else "")
        for comparison in result.verdict.dimensions
    )
    limitations = tuple(
        dict.fromkeys(
            (*result.comparability.limitations, *result.verdict.limitations, *result.verdict.blocking_failures, *result.limitations)
        )
    )
    if limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {limitation}" for limitation in limitations)
    return "\n".join(lines) + "\n"
