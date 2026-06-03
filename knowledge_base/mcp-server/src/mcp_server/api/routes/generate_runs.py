"""Extended generate run endpoints for template snapshot and comparison."""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from mcp_server.config import config
from mcp_server.db.models import GenerateRun
from mcp_server.db.session import async_session_factory
from mcp_server.services.project_registry import ProjectRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generate-runs"])


def _load_run_metrics(project_id: str, run_id: int) -> dict | None:
    metrics_path = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
    if not metrics_path.exists():
        return None
    try:
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _metric_delta(run_a: dict, run_b: dict, key: str) -> float | int | None:
    va = run_a.get(key)
    vb = run_b.get(key)
    if va is None or vb is None:
        return None
    try:
        return round(vb - va, 6)
    except TypeError:
        return None


def _pct_delta(run_a: dict, run_b: dict, key: str) -> float | None:
    va = run_a.get(key)
    delta = _metric_delta(run_a, run_b, key)
    if va in (None, 0) or delta is None:
        return None
    try:
        return round((delta / va) * 100, 2)
    except TypeError:
        return None


def _quality_overall(metrics: dict | None, fallback: float | None) -> float | None:
    quality = (metrics or {}).get("doc_quality") or {}
    return quality.get("overall", fallback)


def _quality_details(metrics: dict | None) -> dict | None:
    if not metrics:
        return None
    return metrics.get("doc_quality")


def _run_summary(run, metrics: dict | None) -> dict:
    analysis_model = (metrics or {}).get("analysis_model") or getattr(run, "model_name", None)
    write_model = (metrics or {}).get("write_model") or getattr(run, "write_model_name", None)
    quality = _quality_overall(metrics, getattr(run, "quality_score", None))
    return {
        "id": run.id,
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "doc_count": run.doc_count,
        "provider": (metrics or {}).get("provider"),
        "analysis_model": analysis_model,
        "model_name": analysis_model,
        "write_model": write_model,
        "write_model_name": write_model,
        "quality": quality,
        "quality_score": quality,
        "quality_pass": quality is not None and quality >= 4.0,
        "doc_quality": _quality_details(metrics),
        "cost_usd": (metrics or {}).get("cost_usd", run.cost_usd),
        "duration_ms": (metrics or {}).get("duration_ms", run.duration_ms),
        "input_tokens": (metrics or {}).get("input_tokens", run.input_tokens),
        "output_tokens": (metrics or {}).get("output_tokens", run.output_tokens),
        "num_turns": (metrics or {}).get("num_turns", run.num_turns),
        "metrics_found": metrics is not None,
        "auto_verdict": getattr(run, "auto_verdict", None),
        "auto_baseline_run_id": getattr(run, "auto_baseline_run_id", None),
    }


async def build_run_comparison(
    project_id: str,
    run_a_id: int,
    run_b_id: int,
    *,
    quality_drop_pct: float = 5.0,
):
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run_map = {r.id: r for r in runs}

        run_a = run_map.get(run_a_id)
        run_b = run_map.get(run_b_id)
        if not run_a or not run_b:
            raise HTTPException(404, "One or both runs not found")

        metrics_a = _load_run_metrics(project_id, run_a_id)
        metrics_b = _load_run_metrics(project_id, run_b_id)
        summary_a = _run_summary(run_a, metrics_a)
        summary_b = _run_summary(run_b, metrics_b)

    delta = {
        key: _metric_delta(summary_a, summary_b, key)
        for key in ("doc_count", "cost_usd", "duration_ms", "input_tokens", "output_tokens", "num_turns", "quality")
    }
    delta_pct = {
        key: _pct_delta(summary_a, summary_b, key)
        for key in ("cost_usd", "duration_ms", "input_tokens", "output_tokens", "num_turns", "quality")
    }

    checks: list[dict] = []
    qa = summary_a.get("quality")
    qb = summary_b.get("quality")
    if qa is not None and qb is not None:
        quality_change_pct = _pct_delta(summary_a, summary_b, "quality")
        quality_drop = -(quality_change_pct or 0.0)
        checks.append({
            "metric": "quality",
            "run_a": qa,
            "run_b": qb,
            "delta": delta["quality"],
            "pct_change": quality_change_pct,
            "threshold": quality_drop_pct,
            "result": "FAIL" if quality_drop > quality_drop_pct else "PASS",
        })
    if metrics_a is None or metrics_b is None:
        checks.append({
            "metric": "metrics",
            "run_a": metrics_a is not None,
            "run_b": metrics_b is not None,
            "result": "WARN",
        })

    verdict = "FAIL" if any(c["result"] == "FAIL" for c in checks) else "PASS"

    return {
        "project_id": project_id,
        "run_a": summary_a,
        "run_b": summary_b,
        "delta": delta,
        "delta_pct": delta_pct,
        "quality_delta": delta["quality"],
        "quality_delta_pct": delta_pct["quality"],
        "verdict": verdict,
        "checks": checks,
    }


@router.get("/generate-runs/{project_id}/compare")
async def compare_runs(project_id: str, a: int, b: int):
    """Compare two generation runs: profiles, templates, and output metrics."""
    return await build_run_comparison(project_id, a, b)


class RerunRequest(BaseModel):
    phase: Literal["scorecard", "doc"]
    target: str | None = None  # required when phase == "doc"


@router.post("/generate-runs/{project_id}/{run_id}/rerun")
async def rerun_phase(
    project_id: str,
    run_id: int,
    req: RerunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Rerun a single harness phase (scorecard or one doc) without rerunning the full pipeline.

    Prerequisites:
    - Parent run must be completed or failed (not running).
    - Phase 2 analysis artifacts must exist in the staging dir.
    - For doc reruns, target must be the doc stem (e.g. '02_COMPONENT_INVENTORY').
    """
    if req.phase == "doc" and not req.target:
        raise HTTPException(status_code=422, detail="target is required when phase is 'doc'")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)

        # Verify parent run exists and belongs to this project
        parent_run = await registry.get_generate_run(run_id)
        if parent_run is None or parent_run.project_id != project_id:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found for project {project_id}")
        if parent_run.status == "running":
            raise HTTPException(status_code=409, detail="Parent run is still running; wait for it to complete")

        # Verify staging prerequisites
        staging_dir = config.project_root / "output" / project_id / "docs_staging"
        analysis_dir = staging_dir / "_analysis"
        if not staging_dir.exists() or not any(analysis_dir.glob("*.md")):
            raise HTTPException(
                status_code=409,
                detail="No Phase 2 analysis artifacts found in staging; run full generation first",
            )
        if req.phase == "doc":
            target_stem = req.target.removesuffix(".md")
            if not (staging_dir / f"{target_stem}.md").exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"Doc {target_stem}.md does not exist in staging; run full generation first",
                )

        # Create child run record
        project = await registry.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

        new_run = await registry.create_generate_run(
            project_id=project_id,
            triggered_by="api",
            model_name=parent_run.model_name,
        )
        new_run_id = new_run.id
        # Record parent link
        from sqlalchemy import update
        await db.execute(
            update(GenerateRun).where(GenerateRun.id == new_run_id).values(parent_run_id=run_id)
        )
        await db.commit()

    log_mgr = request.app.state.log_manager

    background_tasks.add_task(
        _run_phase_rerun_background,
        project_id=project_id,
        run_id=new_run_id,
        parent_run_id=run_id,
        phase=req.phase,
        target=req.target,
        log_mgr=log_mgr,
    )

    return {
        "run_id": new_run_id,
        "parent_run_id": run_id,
        "phase": req.phase,
        "target": req.target,
        "status": "running",
    }


async def _run_phase_rerun_background(
    *,
    project_id: str,
    run_id: int,
    parent_run_id: int,
    phase: str,
    target: str | None,
    log_mgr,
) -> None:
    """Background task: invalidate the target artifact and resume the full pipeline.

    Resume mode skips all phases that have accepted artifacts in staging, so only
    the invalidated artifact will be regenerated.  This is correct and safe because:
    - Phase 1/2 artifacts are never deleted — they are preserved.
    - Only the specific scorecard.md or doc.md is deleted before resuming.
    - The resumed run inherits the same staging dir as the parent run.
    """
    import datetime
    import time

    from sqlalchemy import update as sa_update
    from mcp_server.db.models import GenerateRun
    from mcp_server.db.session import async_session_factory
    from mcp_server.api.routes.projects import _run_generate_background

    start_ms = int(time.time() * 1000)
    staging_dir = config.project_root / "output" / project_id / "docs_staging"

    async def _log(msg: str, level: str = "info") -> None:
        await log_mgr.emit(project_id, msg, level=level)

    try:
        await _log(f"[rerun] Starting phase rerun: phase={phase} target={target}")

        # Invalidate the target artifact so the resumed run regenerates it.
        if phase == "scorecard":
            scorecard = staging_dir / "_assessment" / "scorecard.md"
            scorecard.unlink(missing_ok=True)
            # Also remove the agent task checkpoint so resume doesn't skip it.
            scorecard_task = staging_dir / "_agent_tasks" / "scorecard" / "scorecard" / "status.json"
            scorecard_task.unlink(missing_ok=True)
        elif phase == "doc" and target:
            target_stem = target.removesuffix(".md")
            (staging_dir / f"{target_stem}.md").unlink(missing_ok=True)
            # Remove the agent task checkpoint for this doc.
            doc_task_status = staging_dir / "_agent_tasks" / "phase3_doc" / target_stem / "status.json"
            doc_task_status.unlink(missing_ok=True)

        await _log(f"[rerun] Artifact invalidated; resuming pipeline with resume=True")

        # Run the full pipeline with resume=True — it will skip completed phases and
        # only regenerate the invalidated artifact.
        await _run_generate_background(
            project_id,
            run_id,
            log_mgr,
            resume=True,
        )

    except Exception as exc:
        logger.exception("Phase rerun failed for %s/%s phase=%s", project_id, run_id, phase)
        await _log(f"[rerun] Phase rerun failed: {exc}", level="error")
        async with async_session_factory() as db:
            await db.execute(
                sa_update(GenerateRun)
                .where(GenerateRun.id == run_id)
                .values(
                    status="failed",
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    error_message=str(exc)[:500],
                )
            )
            await db.commit()
