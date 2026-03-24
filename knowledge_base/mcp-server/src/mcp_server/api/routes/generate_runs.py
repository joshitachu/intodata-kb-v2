"""Extended generate run endpoints for template snapshot and comparison."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from mcp_server.db.session import async_session_factory
from mcp_server.services.project_registry import ProjectRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generate-runs"])


@router.get("/generate-runs/{project_id}/{run_id}/template-snapshot")
async def get_template_snapshot(project_id: str, run_id: int):
    """View the exact template versions used in a generation run."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run = next((r for r in runs if r.id == run_id), None)
        if not run:
            raise HTTPException(404, f"Run not found: {run_id}")
        return {
            "run_id": run_id,
            "project_id": project_id,
            "template_profile_id": getattr(run, "template_profile_id", None),
            "template_snapshot": getattr(run, "template_snapshot", None),
        }


@router.get("/generate-runs/{project_id}/compare")
async def compare_runs(project_id: str, a: int, b: int):
    """Compare two generation runs: profiles, templates, and output metrics."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run_map = {r.id: r for r in runs}

        run_a = run_map.get(a)
        run_b = run_map.get(b)
        if not run_a or not run_b:
            raise HTTPException(404, "One or both runs not found")

        def _run_summary(r):
            return {
                "id": r.id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "template_profile_id": getattr(r, "template_profile_id", None),
                "template_snapshot": getattr(r, "template_snapshot", None),
                "doc_count": r.doc_count,
                "cost_usd": r.cost_usd,
                "duration_ms": r.duration_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
            }

        snap_a = getattr(run_a, "template_snapshot", None) or {}
        snap_b = getattr(run_b, "template_snapshot", None) or {}
        all_slugs = sorted(set(list(snap_a.keys()) + list(snap_b.keys())))

        template_diffs = []
        for slug in all_slugs:
            va = snap_a.get(slug)
            vb = snap_b.get(slug)
            if va != vb:
                template_diffs.append({
                    "slug": slug,
                    "version_a": va,
                    "version_b": vb,
                })

        return {
            "project_id": project_id,
            "run_a": _run_summary(run_a),
            "run_b": _run_summary(run_b),
            "template_diffs": template_diffs,
            "total_template_diffs": len(template_diffs),
        }
