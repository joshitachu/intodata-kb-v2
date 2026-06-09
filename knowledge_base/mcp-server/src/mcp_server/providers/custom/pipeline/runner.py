"""PipelineRunner — orchestrates the sequence of Phase objects for a doc generation run."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult


@dataclass
class RunEstimate:
    phases: list[PhaseEstimate]
    total_calls_min: int
    total_calls_max: int
    projected_cost_usd_min: float | None
    projected_cost_usd_max: float | None
    skipped_phases: list[str] = field(default_factory=list)


class PipelineRunner:
    def __init__(self, phases: list[Phase]) -> None:
        self._phases = phases

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        plan: "ExecutionPlan",
        log_cb: "LogCallback",
    ) -> list[PhaseResult]:
        """Run all phases in sequence. Raises on first fatal failure."""
        results: list[PhaseResult] = []
        for phase in self._phases:
            if phase.should_skip(plan):
                results.append(PhaseResult(phase.name, success=True, skipped=True))
                await log_cb(ctx.project_id, f"[pipeline] {phase.name}: skipped", "info")
                continue
            if ctx.resume_state is not None:
                result = await phase.resume(ctx, state, log_cb)
            else:
                result = await phase.run(ctx, state, log_cb)
            results.append(result)
            if not result.success:
                raise RuntimeError(
                    f"Phase {phase.name} failed: {result.error or 'unknown error'}"
                )
        return results

    def estimate(
        self,
        ctx: "DocGenContext",
        plan: "ExecutionPlan",
        model_name: str | None = None,
    ) -> RunEstimate:
        """Return a deterministic call-count and cost estimate without executing anything."""
        groups = list(plan.groups.keys()) if plan.groups else list(ctx.preprocess_groups)
        docs = list(plan.required_docs) if plan.required_docs else []
        unit_count = ctx.total_jobs
        phase_estimates: list[PhaseEstimate] = []
        skipped: list[str] = []
        total_min = 0
        total_max = 0
        for phase in self._phases:
            est = phase.estimate(groups, docs, unit_count)
            if phase.should_skip(plan):
                est = PhaseEstimate(phase.name, 0, 0, skipped=True)
                skipped.append(phase.name)
            phase_estimates.append(est)
            total_min += est.calls_min
            total_max += est.calls_max
        cost_min, cost_max = _project_cost(total_min, total_max, model_name)
        return RunEstimate(
            phases=phase_estimates,
            total_calls_min=total_min,
            total_calls_max=total_max,
            projected_cost_usd_min=cost_min,
            projected_cost_usd_max=cost_max,
            skipped_phases=skipped,
        )


def _project_cost(
    calls_min: int,
    calls_max: int,
    model_name: str | None,
) -> tuple[float | None, float | None]:
    """Rough cost projection: 80K input + 4K output tokens per call."""
    try:
        from mcp_server.services.model_pricing import load_model_pricing
        pricing_table = load_model_pricing()
    except Exception:
        return None, None
    rate = None
    candidates = _model_pricing_candidates(model_name)
    for candidate in candidates:
        if candidate in pricing_table:
            rate = pricing_table[candidate]
            break
    if rate is None:
        return None, None
    avg_input = 80_000
    avg_output = 4_000
    try:
        cost_per_call = (
            (avg_input / 1_000_000) * rate.input_per_million
            + (avg_output / 1_000_000) * rate.output_per_million
        )
    except AttributeError:
        return None, None
    return (
        round(calls_min * cost_per_call, 4),
        round(calls_max * cost_per_call, 4),
    )


def _model_pricing_candidates(model_name: str | None) -> list[str]:
    if not model_name:
        return []
    # Try exact match, then provider/model, then just the model suffix
    parts = model_name.split("/")
    candidates = [model_name]
    if len(parts) > 1:
        candidates.append(parts[-1])
    return candidates
