"""Extended generate run endpoints for template snapshot and comparison."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from mcp_server.config import config
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
