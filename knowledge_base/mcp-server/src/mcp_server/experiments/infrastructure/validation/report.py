"""Deterministic JSON-safe and Markdown rendering for immutable evaluation results."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any

from mcp_server.experiments.domain.evaluation import DimensionComparison, EvaluationResult


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


def render_json_report(result: EvaluationResult) -> str:
    """Serialize exact values deterministically; Decimal values remain strings."""
    return json.dumps(_json_safe(result), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def compare_numeric_dimension(
    dimension: str,
    baseline: Decimal | int | None,
    candidate: Decimal | int | None,
    *,
    lower_is_better: bool,
) -> DimensionComparison:
    """Compare only known values; absent values are never represented as zero."""
    missing = []
    if baseline is None:
        missing.append("baseline value is unavailable")
    if candidate is None:
        missing.append("candidate value is unavailable")
    if missing:
        return DimensionComparison(dimension, "incomparable", tuple(missing))
    if baseline == candidate:
        return DimensionComparison(dimension, "mixed", ("values are equal",))
    candidate_is_better = candidate < baseline if lower_is_better else candidate > baseline
    return DimensionComparison(dimension, "better" if candidate_is_better else "worse")


def _display(value: object | None) -> str:
    return "unknown" if value is None else str(value)


def render_markdown_report(result: EvaluationResult) -> str:
    """Render a human-readable, informational-only comparison report."""
    lines = [
        "# Agentic Experiment Comparison",
        "",
        "Informational only. This report cannot publish candidate documentation.",
        "",
        "## Evaluator Receipt",
        "",
        f"- Evaluator: `{result.receipt.evaluator_id}` v{result.receipt.evaluator_version}",
        f"- Code SHA-256: `{result.receipt.code_sha256}`",
        f"- Configuration SHA-256: `{result.receipt.configuration_sha256}`",
        "",
        "## Document Alignment",
        "",
        "| Document | Status |",
        "| --- | --- |",
    ]
    lines.extend(f"| {pair.document_id} | {pair.status} |" for pair in result.alignment.pairs)
    lines.extend(
        [
            "",
            "## Runtime and Cost",
            "",
            "| Metric | Baseline | Candidate |",
            "| --- | ---: | ---: |",
            f"| Generation wall (ms) | {_display(result.baseline_timing.generation_wall_ms)} | {_display(result.candidate_timing.generation_wall_ms)} |",
            f"| Provider latency sum (ms) | {_display(result.baseline_timing.provider_latency_sum_ms)} | {_display(result.candidate_timing.provider_latency_sum_ms)} |",
            f"| Critical path (ms) | {_display(result.baseline_timing.critical_path_ms)} | {_display(result.candidate_timing.critical_path_ms)} |",
            f"| Input tokens | {_display(result.baseline_usage.input_tokens)} | {_display(result.candidate_usage.input_tokens)} |",
            f"| Output tokens | {_display(result.baseline_usage.output_tokens)} | {_display(result.candidate_usage.output_tokens)} |",
            f"| Provider billed cost (USD) | {_display(result.baseline_cost.billed_cost_usd)} | {_display(result.candidate_cost.billed_cost_usd)} |",
            f"| Normalized cost (USD) | {_display(result.baseline_cost.normalized_cost_usd)} | {_display(result.candidate_cost.normalized_cost_usd)} |",
            "",
            "## Informational Verdict",
            "",
            f"Candidate verdict: **{result.verdict.candidate_verdict}**",
        ]
    )
    for dimension in result.verdict.dimensions:
        limitation = "; ".join(dimension.limitations) or "none"
        lines.append(f"- {dimension.dimension}: **{dimension.verdict}** (limitations: {limitation})")
    all_limitations = (
        *result.comparability.limitations,
        *result.verdict.limitations,
        *result.verdict.blocking_failures,
        *result.limitations,
    )
    if all_limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {limitation}" for limitation in dict.fromkeys(all_limitations))
    return "\n".join(lines) + "\n"
