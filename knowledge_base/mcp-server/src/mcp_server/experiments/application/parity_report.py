"""Deterministic non-sensitive reports for single-document parity evaluation."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
import json
from typing import Any

from mcp_server.experiments.application.evaluate_parity import SingleDocumentParityResult


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


def _side_payload(result: SingleDocumentParityResult, side: str) -> dict[str, Any]:
    observation = result.baseline if side == "baseline" else result.candidate
    return {
        "artifact": _json_safe(observation.artifact),
        "cost": _json_safe(observation.cost),
        "provenance": _json_safe(observation.provenance),
        "timing": _json_safe(observation.timing),
        "usage": _json_safe(observation.usage),
    }


def canonical_parity_payload(result: SingleDocumentParityResult) -> dict[str, Any]:
    """Return a report payload without raw prompts, envelopes, or document content."""
    return {
        "baseline": _side_payload(result, "baseline"),
        "baseline_contract": _json_safe(result.baseline_contract),
        "candidate": _side_payload(result, "candidate"),
        "candidate_contract": _json_safe(result.candidate_contract),
        "comparability": _json_safe(result.comparability),
        "comparisons": _json_safe(result.comparisons),
        "document_id": result.document_id,
        "limitations": list(result.limitations),
        "output_contract": _json_safe(result.output_contract),
        "verdict": _json_safe(result.verdict),
    }


def render_canonical_json_report(result: SingleDocumentParityResult) -> str:
    """Render stable JSON suitable for a hash-addressed evaluation artifact."""
    return json.dumps(
        canonical_parity_payload(result), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) + "\n"


def _display(value: object | None) -> str:
    return "unknown" if value is None else str(value)


def _metric_lines(result: SingleDocumentParityResult) -> list[str]:
    baseline = result.baseline
    candidate = result.candidate
    return [
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        f"| Input tokens | {_display(baseline.usage.input_tokens)} | {_display(candidate.usage.input_tokens)} |",
        f"| Output tokens | {_display(baseline.usage.output_tokens)} | {_display(candidate.usage.output_tokens)} |",
        f"| Wall (ms) | {_display(baseline.timing.experiment_wall_ms)} | {_display(candidate.timing.experiment_wall_ms)} |",
        f"| Provider latency sum (ms) | {_display(baseline.timing.provider_latency_sum_ms)} | {_display(candidate.timing.provider_latency_sum_ms)} |",
        f"| Tool calls | {_display(baseline.usage.tool_calls)} | {_display(candidate.usage.tool_calls)} |",
        f"| Normalized cost (USD) | {_display(baseline.cost.normalized_cost_usd)} | {_display(candidate.cost.normalized_cost_usd)} |",
        f"| Provider-reported cost (USD) | {_display(baseline.cost.billed_cost_usd)} | {_display(candidate.cost.billed_cost_usd)} |",
    ]


def _provenance_lines(result: SingleDocumentParityResult) -> list[str]:
    baseline = result.baseline.provenance
    candidate = result.candidate.provenance
    return [
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        f"| Citations | {_display(baseline.citation_count)} | {_display(candidate.citation_count)} |",
        f"| Resolved citations | {_display(baseline.resolved_citation_count)} | {_display(candidate.resolved_citation_count)} |",
        f"| Evidence items | {_display(baseline.evidence_item_count)} | {_display(candidate.evidence_item_count)} |",
        f"| Cited evidence items | {_display(baseline.cited_evidence_item_count)} | {_display(candidate.cited_evidence_item_count)} |",
        f"| Unsupported claims | {_display(baseline.unsupported_claim_count)} | {_display(candidate.unsupported_claim_count)} |",
        f"| Extracted facts | {_display(baseline.extracted_fact_count)} | {_display(candidate.extracted_fact_count)} |",
        f"| Matched extracted facts | {_display(baseline.matched_extracted_fact_count)} | {_display(candidate.matched_extracted_fact_count)} |",
    ]


def render_markdown_report(result: SingleDocumentParityResult) -> str:
    """Render a deterministic human report without asserting unconfirmed equivalence."""
    lines = [
        "# Single-Document Parity Evaluation",
        "",
        "Informational only. This report cannot publish or promote a candidate output.",
        "",
        "## Output",
        "",
        f"- Document: `{result.document_id}`",
        f"- Declared artifact: `{result.output_contract.artifact_name}` ({result.output_contract.media_type})",
        "",
        "## Declared Output Contract",
        "",
        "| Branch | Status |",
        "| --- | --- |",
        f"| Baseline | {result.baseline_contract.status} |",
        f"| Candidate | {result.candidate_contract.status} |",
        "",
        "## Prompt and Runtime Comparability",
        "",
        "| Field | Status |",
        "| --- | --- |",
    ]
    fields = (
        result.comparability.semantic_prompt_sha256,
        result.comparability.context_manifest_sha256,
        result.comparability.source_snapshot_sha256,
        result.comparability.output_contract_sha256,
        result.comparability.model_route_options_sha256,
        result.comparability.runtime_envelope_sha256,
    )
    lines.extend(f"| {field.name} | {field.status} |" for field in fields)
    lines.extend(
        [
            "",
            f"Semantic parity confirmed: **{'yes' if result.comparability.semantic_parity_confirmed else 'no'}**",
            f"Semantic input status: **{result.comparability.semantic_status}**",
            f"Runtime envelope status: **{result.comparability.runtime_status}**",
            "",
            "## Runtime and Cost",
            "",
            *_metric_lines(result),
            "",
            "## Evidence, Provenance, and Citations",
            "",
            *_provenance_lines(result),
            "",
            "## Verdict",
            "",
            f"Candidate verdict: **{result.verdict.candidate_verdict}**",
        ]
    )
    lines.extend(
        f"- {comparison.dimension}: **{comparison.verdict}**"
        + (f" ({'; '.join(comparison.limitations)})" if comparison.limitations else "")
        for comparison in result.comparisons
    )
    if result.limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {limitation}" for limitation in result.limitations)
    return "\n".join(lines) + "\n"
