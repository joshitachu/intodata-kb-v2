"""Report rendering: plain text for humans, JSON for CI.

The text report leads with a provenance banner. That placement is deliberate --
the single most damaging thing this harness could do is let a hand-written
fixture score be quoted as "the system's groundedness". If fewer than 100% of
the answers in a run were captured from a live system, the header says so before
any number appears.
"""

from __future__ import annotations

import json
from typing import Any

from evals.runner import Metric, RunResult

_RULE = "=" * 78
_THIN = "-" * 78


def _fmt(metric: Metric) -> str:
    if not metric.defined:
        return "n/a"
    value = metric.value
    assert value is not None
    return f"{value:6.1%}  (n={metric.denominator})"


def render_text(result: RunResult, *, verbose: bool = False) -> str:
    lines: list[str] = []
    add = lines.append

    add(_RULE)
    add(f"ANSWER-QUALITY EVAL -- {result.suite_name} v{result.suite_version}")
    add(_RULE)
    add(f"answer provider : {result.provider_name}")
    if result.judge_name == "none":
        judge_note = " (judge-dependent metrics not computed)"
    elif result.judge_deterministic:
        judge_note = " (deterministic proxy -- lexical overlap, not entailment)"
    else:
        judge_note = " (model; not reproducible, informational only)"
    add(f"judge           : {result.judge_name}{judge_note}")
    add(f"cases           : {len(result.results)}")
    add(f"duration        : {result.duration_s:.2f}s")
    add("")

    # --- provenance banner -------------------------------------------------
    if result.measured_fraction < 1.0:
        add("!! NOT A MEASUREMENT OF THE LIVE SYSTEM !!")
        add(
            f"   Only {result.measured_fraction:.0%} of the answers in this run were captured"
        )
        add("   from a running system. The rest are recorded or authored fixtures,")
        add("   present to exercise the scorer. Do not quote these numbers as a")
        add("   baseline for the RAG pipeline.")
    else:
        add("Answer provenance: 100% live capture. These numbers are a measurement.")
    add("   provenance breakdown: " + ", ".join(f"{k}={v}" for k, v in result.provenance.items()))
    add("")

    # --- metrics -----------------------------------------------------------
    add("METRICS")
    add(_THIN)
    deterministic_keys = {
        "retrieval_hit_rate",
        "groundedness_numeric",
        "citation_validity",
        "abstention_recall",
        "over_abstention_rate",
        "coverage",
        "undisclosed_partial_rate",
        "count_accuracy",
        "contradiction_disclosure",
        "must_not_claim_violation_rate",
        "pass_rate",
    }
    for group, title in (("det", "Deterministic"), ("judge", "Judge-dependent")):
        add(f"  {title}:")
        for metric in result.metrics:
            is_det = metric.key in deterministic_keys
            if (group == "det") != is_det:
                continue
            arrow = "^" if metric.higher_is_better else "v"
            add(f"    {arrow} {metric.label:<44s} {_fmt(metric)}")
        add("")

    # --- failures ----------------------------------------------------------
    failing = result.failing
    add(f"CASE RESULTS -- {len(result.results) - len(failing)} passed, {len(failing)} failed")
    add(_THIN)
    for item in result.results:
        status = "PASS" if item.score.passed else "FAIL"
        kind = "answerable" if item.case.answerable else "UNANSWERABLE"
        add(f"  [{status}] {item.case.id:<38s} {kind}")
        if not item.score.passed:
            for failure in item.score.failures:
                add(f"         - {failure}")
        if verbose:
            cov = item.score.coverage
            if cov:
                add(
                    f"         coverage {cov.ratio:.0%} "
                    f"found={list(cov.found)} missing={list(cov.missing)}"
                )
            if item.verdict is not None:
                add(
                    f"         judge[{item.verdict.judge}] grounded="
                    f"{item.verdict.groundedness} correct={item.verdict.correctness}"
                )
    add("")
    add(_RULE)
    add(f"PASS RATE: {result.pass_rate:.1%}  ({len(result.results) - len(failing)}/{len(result.results)})")
    add(_RULE)
    return "\n".join(lines)


def render_json(result: RunResult) -> str:
    payload: dict[str, Any] = {
        "suite": {"name": result.suite_name, "version": result.suite_version},
        "provider": result.provider_name,
        "judge": {"name": result.judge_name, "deterministic": result.judge_deterministic},
        "provenance": result.provenance,
        "measured_fraction": result.measured_fraction,
        "is_measurement": result.measured_fraction >= 1.0,
        "duration_s": round(result.duration_s, 3),
        "pass_rate": result.pass_rate,
        "metrics": [
            {
                "key": m.key,
                "label": m.label,
                "value": m.value,
                "n": m.denominator,
                "higher_is_better": m.higher_is_better,
                "description": m.description,
            }
            for m in result.metrics
        ],
        "cases": [
            {
                "id": r.case.id,
                "project": r.case.project,
                "answerable": r.case.answerable,
                "answer_type": r.case.answer_type,
                "provenance": r.recorded.provenance,
                "passed": r.score.passed,
                "failures": list(r.score.failures),
                "coverage": (
                    {
                        "ratio": r.score.coverage.ratio,
                        "found": list(r.score.coverage.found),
                        "missing": list(r.score.coverage.missing),
                        "disclosed": r.score.coverage.disclosed,
                    }
                    if r.score.coverage
                    else None
                ),
                "retrieval_hit": r.score.retrieval_hit,
                "abstained": r.score.abstained,
                "abstention_correct": r.score.abstention_correct,
                "over_abstained": r.score.over_abstained,
                "counts_correct": r.score.counts_correct,
                "wrong_counts": list(r.score.wrong_counts),
                "contradiction_disclosed": r.score.contradiction_disclosed,
                "groundedness_numeric": r.score.groundedness.numeric_ratio,
                "ungrounded_numbers": list(r.score.groundedness.ungrounded),
                "judge": (
                    {
                        "judge": r.verdict.judge,
                        "groundedness": r.verdict.groundedness,
                        "correctness": r.verdict.correctness,
                        "confidence": r.verdict.confidence,
                        "unsupported_claims": list(r.verdict.unsupported_claims),
                    }
                    if r.verdict
                    else None
                ),
            }
            for r in result.results
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
