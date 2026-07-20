"""Target-neutral execution and deterministic report aggregation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from .grader import grade_scenario
from .models import (
    BudgetTrace,
    EvaluationReport,
    EvaluationSummary,
    RunTrace,
    RuntimeAttribution,
    Scenario,
    ScenarioGrade,
    StateExpectation,
)
from .security import contains_secret_shape, require_sanitized


TraceResult = RunTrace | list[RunTrace]


class EvaluationTarget(Protocol):
    name: str

    def run(self, scenario: Scenario) -> TraceResult: ...


class ScriptedTraceTarget:
    """Deterministic fake-model/runtime-mechanics seam; not a semantic accuracy target."""

    def __init__(self, traces: dict[str, TraceResult]) -> None:
        self.name = "scripted-runtime-mechanics"
        self._traces = traces

    def run(self, scenario: Scenario) -> TraceResult:
        return self._traces[scenario.scenario_id]


class LocalIntegrationTarget:
    """Adapter for a local API/integration fixture that returns the neutral trace schema."""

    name = "local-api-integration"

    def __init__(self, invoke: Callable[[Scenario], TraceResult]) -> None:
        self._invoke = invoke

    def run(self, scenario: Scenario) -> TraceResult:
        return self._invoke(scenario)


class AuthenticatedDeployedApiTarget:
    """Later-slice adapter; credential ownership stays inside the injected API invoker.

    Slice 0 deliberately supplies no HTTP implementation or credential. The invoker
    must return the same provider-neutral trace used by local, shadow, and canary runs.
    """

    name = "authenticated-deployed-api"

    def __init__(self, invoke: Callable[[Scenario], TraceResult]) -> None:
        self._invoke = invoke

    def run(self, scenario: Scenario) -> TraceResult:
        return self._invoke(scenario)


class RecordedTraceTarget:
    """Offline target for sanitized historical captures; never executes a capability."""

    def __init__(self, path: Path, *, name: str = "current-custom-runtime-recorded-v1") -> None:
        raw = json.loads(path.read_text())
        self.name = name
        self._traces = {
            trace.scenario_id: trace
            for trace in (RunTrace.model_validate(item) for item in raw["traces"])
        }
        self._fallback_attribution = RuntimeAttribution.model_validate(raw["attribution"])

    def run(self, scenario: Scenario) -> RunTrace:
        trace = self._traces.get(scenario.scenario_id)
        if trace:
            return trace
        return RunTrace(
            scenario_id=scenario.scenario_id,
            turn_id=scenario.turns[0].turn_id,
            status="not_evaluated",
            captured_dimensions=[],
            attribution=self._fallback_attribution,
            boundary_decisions=[],
            tool_calls=[],
            observations=[],
            state=StateExpectation(),
            effects=[],
            response="",
            evidence_refs=[],
            budget=BudgetTrace(budget_class=scenario.turns[0].expected.budget_class),
            notes="No sanitized historical capture exists for this family.",
        )


def evaluate_target(
    scenarios: list[Scenario],
    target: EvaluationTarget,
) -> EvaluationReport:
    grades: list[ScenarioGrade] = []
    traces: list[RunTrace] = []
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    taxonomy = Counter[str]()
    for scenario in scenarios:
        result = target.run(scenario)
        grade = grade_scenario(scenario, result)
        result_traces = result if isinstance(result, list) else [result]
        traces.extend(result_traces)
        grades.append(grade)
        family_counts[scenario.family][grade.evaluator_status] += 1
        for failure in grade.failures:
            taxonomy[failure.taxonomy.value] += 1
    safe_traces = [
        trace
        for trace in traces
        if not contains_secret_shape(trace.attribution.model_dump(mode="json"))
    ]
    attributions = sorted({
        json.dumps(trace.attribution.model_dump(mode="json"), sort_keys=True)
        for trace in safe_traces
    })
    generated_at = max(
        (trace.attribution.captured_at for trace in safe_traces),
        default="not-available",
    )
    report = EvaluationReport(
        corpus_version="agent-runtime-behavior-v1",
        target=target.name,
        generated_at=generated_at,
        attributions=[RuntimeAttribution.model_validate_json(item) for item in attributions],
        summary=EvaluationSummary(
            total=len(grades),
            evaluated=sum(grade.evaluated for grade in grades),
            passed=sum(grade.passed for grade in grades),
            product_failed=sum(grade.evaluated and not grade.passed for grade in grades),
            not_evaluated=sum(
                grade.evaluator_status == "evaluator_gap" for grade in grades
            ),
            evaluator_invalid=sum(
                grade.evaluator_status == "evaluator_invalid" for grade in grades
            ),
            by_taxonomy=dict(sorted(taxonomy.items())),
            by_family={family: dict(sorted(counts.items())) for family, counts in sorted(family_counts.items())},
        ),
        grades=grades,
    )
    require_sanitized(report.model_dump(mode="json"), label="evaluation report")
    return report


def render_markdown(report: EvaluationReport) -> str:
    summary = report.summary
    lines = [
        "# Nexus Agent Runtime — Current Baseline",
        "",
        f"- Corpus: `{report.corpus_version}`",
        f"- Target: `{report.target}`",
        f"- Capture watermark: `{report.generated_at}`",
        f"- Scenarios: {summary.total}",
        f"- Evaluated historical captures: {summary.evaluated}",
        f"- Product passes: {summary.passed}",
        f"- Product failures: {summary.product_failed}",
        f"- Not evaluated (coverage gaps): {summary.not_evaluated}",
        f"- Evaluator-invalid traces: {summary.evaluator_invalid}",
        "",
        "This is an offline, sanitized recorded-trace baseline. It performs no provider call and no effect. Missing historical captures are evaluator gaps, not product passes or failures.",
        "",
        "## Failures by taxonomy",
        "",
        "| Taxonomy | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in summary.by_taxonomy.items())
    lines.extend(["", "## Evaluated failure detail", "", "| Scenario | Primary failure | Detail |", "|---|---|---|"])
    for grade in report.grades:
        if not grade.evaluated or grade.passed:
            continue
        failure = grade.failures[0]
        detail = failure.message.replace("|", "\\|")
        lines.append(f"| `{grade.scenario_id}` | `{failure.taxonomy.value}` | {detail} |")
    lines.extend([
        "",
        "## Historical capture dimension gaps",
        "",
        "These dimensions were not present in the sanitized source capture and do not contribute product taxonomy.",
        "",
        "| Scenario | Uncaptured dimensions |",
        "|---|---|",
    ])
    for grade in report.grades:
        if not grade.evaluated or not grade.uncaptured_dimensions:
            continue
        dimensions = ", ".join(item.value for item in grade.uncaptured_dimensions)
        lines.append(f"| `{grade.scenario_id}` | `{dimensions}` |")
    lines.extend(["", "## Attribution", ""])
    for item in report.attributions:
        lines.append(
            f"- runtime `{item.runtime_version}`; model `{item.model_profile}`; prompt `{item.prompt_version}`; manifest `{item.manifest_hash}`; transport `{item.transport}`"
        )
    rendered = "\n".join(lines) + "\n"
    require_sanitized(rendered, label="rendered report")
    return rendered
