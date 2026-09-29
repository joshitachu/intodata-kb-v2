"""GET /projects/{project_id}/generate/estimate — dry-run cost and call estimate."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["generate"])


class PhaseEstimateOut(BaseModel):
    phase_name: str
    calls_min: int
    calls_max: int
    skipped: bool
    notes: str


class RunEstimateOut(BaseModel):
    project_id: str
    phases: list[PhaseEstimateOut]
    total_calls_min: int
    total_calls_max: int
    projected_cost_usd_min: float | None
    projected_cost_usd_max: float | None
    skipped_phases: list[str]


@router.get(
    "/projects/{project_id}/generate/estimate",
    response_model=RunEstimateOut,
)
async def get_generate_estimate(project_id: str) -> RunEstimateOut:
    """Return a deterministic call-count and cost estimate without running anything."""
    from mcp_server.config import config
    from mcp_server.providers.custom.validators import build_facts_manifest, REQUIRED_DOCS
    from mcp_server.providers.custom.dag import deterministic_execution_plan
    from mcp_server.providers.custom.pipeline.runner import PipelineRunner
    from mcp_server.providers.custom.pipeline.sequence import build_phases
    from mcp_server.providers.base import DocGenContext

    scan_output_dir: Path = config.intodoc_output_dir / project_id
    if not scan_output_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No scan output found for project '{project_id}'",
        )

    try:
        facts = build_facts_manifest(project_id, scan_output_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    preprocess_groups = list(facts.groups.keys())

    plan = deterministic_execution_plan(
        facts,
        required_docs=list(REQUIRED_DOCS),
        preprocess_groups=preprocess_groups,
        investigation_policy="run",
        analysis_policy="run",
        scorecard_policy="run",
        doc_bundles_policy="run",
        plugin_mode=False,
        small_project_threshold=config.small_project_threshold,
    )

    runner = PipelineRunner(build_phases(helpers=None))

    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=Path("/dev/null"),
        staging_abs="/dev/null",
        logs_abs="/dev/null",
        preprocess_groups=preprocess_groups,
        total_jobs=facts.unit_count,
        total_groups=len(facts.groups),
        effective_model=config.doc_generation_model,
        write_model=config.doc_generation_write_model,
        platform=facts.platform,
        analysis_guide_file="_analysis_prompts*.md",
    )

    estimate = runner.estimate(ctx, plan, model_name=config.doc_generation_model)

    return RunEstimateOut(
        project_id=project_id,
        phases=[
            PhaseEstimateOut(
                phase_name=p.phase_name,
                calls_min=p.calls_min,
                calls_max=p.calls_max,
                skipped=p.skipped,
                notes=p.notes,
            )
            for p in estimate.phases
        ],
        total_calls_min=estimate.total_calls_min,
        total_calls_max=estimate.total_calls_max,
        projected_cost_usd_min=estimate.projected_cost_usd_min,
        projected_cost_usd_max=estimate.projected_cost_usd_max,
        skipped_phases=estimate.skipped_phases,
    )
