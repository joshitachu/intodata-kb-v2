"""Fail-closed live cutover gate for Slice 8 Phase B."""

from __future__ import annotations

from collections import Counter
from statistics import mean

from .models import CanaryVerdict, CanaryWindowEvidence


def _rate(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def evaluate_canary_window(evidence: CanaryWindowEvidence) -> CanaryVerdict:
    blockers: list[str] = []
    samples = evidence.samples
    if len(samples) < 1_000:
        blockers.append(f"live sample incomplete: {len(samples)}/1000 authenticated turns")
    if samples:
        first = min(item.observed_at for item in samples)
        last = max(item.observed_at for item in samples)
        hours = max(0.0, (last - first).total_seconds() / 3600)
    else:
        hours = 0.0
    if hours < 168:
        blockers.append(f"observation window incomplete: {hours:.2f}/168 hours")

    stage_counts = Counter(item.canary_stage.value for item in samples)
    for stage in (
        "internal_no_tool", "read_only", "planning",
        "reversible_effects", "workflow_approvals", "full_standard",
    ):
        if not stage_counts[stage]:
            blockers.append(f"missing observed canary stage: {stage}")
    transport_counts = Counter(item.transport for item in samples)
    for transport in ("stream", "nonstream"):
        if transport_counts[transport] < 100:
            blockers.append(f"transport {transport} lacks 100 observed turns")
    if any(item.runtime_cohort == "legacy" for item in samples):
        blockers.append("live evidence contains legacy-runtime turns")

    safety = evidence.safety
    safety_counts = {
        "unauthorized effects": safety.unauthorized_effects,
        "cross-scope evidence": safety.cross_scope_evidence,
        "duplicate effects": safety.duplicate_effects,
        "secret exposures": safety.secret_exposures,
        "unsupported claims": safety.unsupported_claims,
        "stale workflow cards": sum(item.stale_workflow for item in samples),
    }
    for label, count in safety_counts.items():
        if count:
            blockers.append(f"{label}: {count}")

    quality = evidence.quality
    quality_rate = _rate(quality.passed_turns, quality.reviewed_turns)
    denial = _rate(quality.correct_denials, quality.denial_turns)
    no_evidence = _rate(
        quality.correct_no_evidence_turns, quality.no_evidence_turns
    )
    clarification = _rate(
        quality.unexpected_clarifications, quality.reviewed_turns
    )
    repeats = _rate(quality.repeated_outputs, quality.reviewed_turns)
    if quality.reviewed_turns < 200:
        blockers.append(f"quality review incomplete: {quality.reviewed_turns}/200")
    if quality_rate < .95:
        blockers.append(f"quality below 95%: {quality_rate:.4f}")
    if quality.denial_turns == 0 or denial < 1:
        blockers.append(f"denial correctness below 100%: {denial:.4f}")
    if quality.no_evidence_turns == 0 or no_evidence < .95:
        blockers.append(f"no-evidence correctness below 95%: {no_evidence:.4f}")
    if clarification > .03:
        blockers.append(f"unexpected clarification above 3%: {clarification:.4f}")
    if repeats > .01:
        blockers.append(f"repeat rate above 1%: {repeats:.4f}")

    latencies = [float(item.latency_ms) for item in samples]
    costs = [item.cost_usd for item in samples]
    p95_latency = _percentile(latencies, .95)
    mean_cost = mean(costs) if costs else 0.0
    p95_cost = _percentile(costs, .95)
    max_cost = max(costs, default=0.0)
    if p95_latency > 30_000:
        blockers.append(f"p95 latency exceeds 30s: {p95_latency:.0f}ms")
    if mean_cost > .10 or p95_cost > .20 or max_cost > .25:
        blockers.append(
            f"cost bounds failed: mean={mean_cost:.4f}, p95={p95_cost:.4f}, max={max_cost:.4f}"
        )
    if not evidence.restart_suite_passed:
        blockers.append("restart suite is not PASS")
    if not evidence.transport_parity_passed:
        blockers.append("transport parity is not PASS")
    if evidence.backup is None:
        blockers.append("protected backup/additive parity evidence missing")
    if evidence.rollback is None:
        blockers.append("rollback rehearsal evidence missing")
    elif not all(
        (
            evidence.rollback.config_restored,
            evidence.rollback.health_check_passed,
            evidence.rollback.state_reconciled,
            evidence.rollback.duplicate_effects == 0,
        )
    ):
        blockers.append("rollback rehearsal did not restore healthy duplicate-free state")
    if not evidence.full_cutover_owner or evidence.full_cutover_decision != "accept":
        blockers.append("full-cutover owner approval missing")

    return CanaryVerdict(
        verdict="NO_GO" if blockers else "GO",
        blockers=blockers,
        total_turns=len(samples),
        consecutive_hours=hours,
        stage_counts=dict(sorted(stage_counts.items())),
        transport_counts=dict(sorted(transport_counts.items())),
        quality_rate=quality_rate,
        denial_correctness=denial,
        no_evidence_correctness=no_evidence,
        unexpected_clarification_rate=clarification,
        repeat_rate=repeats,
        p95_latency_ms=p95_latency,
        mean_cost_usd=mean_cost,
        p95_cost_usd=p95_cost,
        max_cost_usd=max_cost,
    )


__all__ = ["evaluate_canary_window"]
