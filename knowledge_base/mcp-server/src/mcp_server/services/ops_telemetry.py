"""Privacy-preserving telemetry push for IntoData Ops.

This module intentionally reports operational metadata only. It must not read
or transmit uploaded files, source code, generated document bodies, chat
content, raw logs, secrets, or encrypted tokens.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.config import config
from mcp_server.db.models import GenerateRun, Project, ScanRun
from mcp_server.db.session import async_session_factory

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "ops.telemetry.v1"
MAX_ERROR_CHARS = 500
LATEST_RUN_LIMIT = 20


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.isoformat()


def _truncate(value: str | None, limit: int = MAX_ERROR_CHARS) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _run_payload(run: GenerateRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "triggered_by": run.triggered_by,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "duration_ms": run.duration_ms,
        "duration_api_ms": run.duration_api_ms,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost_usd": run.cost_usd,
        "num_turns": run.num_turns,
        "doc_count": run.doc_count,
        "model_name": run.model_name,
        "write_model_name": run.write_model_name,
        "quality_score": run.quality_score,
        "auto_verdict": run.auto_verdict,
        "error_summary": _truncate(run.error_message),
    }


def _scan_payload(run: ScanRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "triggered_by": run.triggered_by,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "job_count": run.job_count,
    }


def _project_payload(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "display_name": project.display_name,
        "platform": project.platform,
        "source_type": project.source_type,
        "status": project.status,
        "last_scan_at": _iso(project.last_scan_at),
        "last_generate_at": _iso(project.last_generate_at),
        "last_rag_push_at": _iso(project.last_rag_push_at),
        "job_count": project.job_count,
        "doc_count": project.doc_count,
        "auto_reingest_enabled": project.auto_reingest_enabled,
        "auto_reingest_last_checked_at": _iso(project.auto_reingest_last_checked_at),
        "auto_reingest_error_summary": _truncate(project.auto_reingest_error),
        "webhook_status": "active" if project.github_webhook_id else "not_registered",
        "error_summary": _truncate(project.error_message),
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }


async def _fetch_rag_metrics() -> dict[str, Any]:
    """Fetch user/activity/conversation metrics from the RAG backend."""
    if not config.rag_base_url or not config.ingest_api_key:
        return {}
    url = config.rag_base_url.rstrip("/") + "/api/v1/internal/ops-metrics"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {config.ingest_api_key}"}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch RAG ops metrics: %s", exc)
        return {}


def _build_model_stats(runs: list[GenerateRun]) -> list[dict[str, Any]]:
    """Aggregate per-model quality/cost/speed from recent generate runs."""
    from collections import defaultdict

    buckets: dict[str, list] = defaultdict(list)
    for run in runs:
        key = run.model_name or "unknown"
        buckets[key].append(run)

    stats = []
    for model, model_runs in sorted(buckets.items()):
        completed = [r for r in model_runs if r.status == "completed"]
        quality_vals = [r.quality_score for r in completed if r.quality_score is not None]
        cost_vals = [r.cost_usd for r in completed if r.cost_usd is not None]
        duration_vals = [r.duration_ms for r in completed if r.duration_ms is not None]
        stats.append({
            "model": model,
            "run_count": len(model_runs),
            "avg_quality": round(sum(quality_vals) / len(quality_vals), 2) if quality_vals else None,
            "avg_cost_usd": round(sum(cost_vals) / len(cost_vals), 4) if cost_vals else None,
            "avg_duration_ms": int(sum(duration_vals) / len(duration_vals)) if duration_vals else None,
        })
    return stats


async def build_ops_snapshot(db: AsyncSession) -> dict[str, Any]:
    """Build one safe telemetry snapshot from local MCP metadata tables."""

    now = datetime.datetime.now(datetime.timezone.utc)
    projects = list(
        (
            await db.execute(select(Project).order_by(Project.created_at.desc()))
        ).scalars()
    )
    latest_generate_runs = list(
        (
            await db.execute(
                select(GenerateRun)
                .order_by(GenerateRun.started_at.desc())
                .limit(LATEST_RUN_LIMIT)
            )
        ).scalars()
    )
    latest_scan_runs = list(
        (
            await db.execute(
                select(ScanRun)
                .order_by(ScanRun.started_at.desc())
                .limit(LATEST_RUN_LIMIT)
            )
        ).scalars()
    )

    generate_totals = (
        await db.execute(
            select(
                func.count(GenerateRun.id),
                func.coalesce(func.sum(GenerateRun.input_tokens), 0),
                func.coalesce(func.sum(GenerateRun.output_tokens), 0),
                func.coalesce(func.sum(GenerateRun.cost_usd), 0),
            )
        )
    ).one()

    scan_totals = (
        await db.execute(
            select(
                func.count(ScanRun.id),
                func.coalesce(func.sum(ScanRun.job_count), 0),
            )
        )
    ).one()

    quality_values = [
        run.quality_score for run in latest_generate_runs if run.quality_score is not None
    ]
    average_quality = (
        round(sum(quality_values) / len(quality_values), 3) if quality_values else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "client_slug": config.ops_client_slug,
        "environment": config.ops_environment,
        "release_version": config.ops_release_version,
        "mcp_url": config.public_url.rstrip("/") if config.public_url else None,
        "sent_at": now.isoformat(),
        "health": {"mcp": "ok"},
        "totals": {
            "project_count": len(projects),
            "job_count": sum(p.job_count or 0 for p in projects),
            "doc_count": sum(p.doc_count or 0 for p in projects),
            "generate_run_count": int(generate_totals[0] or 0),
            "scan_run_count": int(scan_totals[0] or 0),
            "scan_job_count": int(scan_totals[1] or 0),
            "input_tokens": int(generate_totals[1] or 0),
            "output_tokens": int(generate_totals[2] or 0),
            "cost_usd": float(generate_totals[3] or 0),
            "latest_run_average_quality": average_quality,
        },
        "projects": [_project_payload(project) for project in projects],
        "latest_generate_runs": [_run_payload(run) for run in latest_generate_runs],
        "latest_scan_runs": [_scan_payload(run) for run in latest_scan_runs],
        "model_stats": _build_model_stats(latest_generate_runs),
    }


async def send_ops_snapshot(snapshot: dict[str, Any]) -> None:
    url = config.ops_telemetry_url.rstrip("/")
    endpoint = f"{url}/ops/clients/{config.ops_client_slug}/telemetry"
    headers = {
        "Authorization": f"Bearer {config.ops_telemetry_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(endpoint, json=snapshot, headers=headers)
        response.raise_for_status()


async def push_once() -> None:
    async with async_session_factory() as db:
        snapshot = await build_ops_snapshot(db)
    rag_metrics = await _fetch_rag_metrics()
    if rag_metrics:
        snapshot["rag"] = rag_metrics
    await send_ops_snapshot(snapshot)


async def telemetry_loop() -> None:
    interval = max(config.ops_telemetry_interval_seconds, 60)
    logger.info("IntoData Ops telemetry enabled; pushing every %ss", interval)
    while True:
        try:
            await push_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("IntoData Ops telemetry push failed: %s", exc)
        await asyncio.sleep(interval)


def telemetry_enabled() -> bool:
    return bool(
        config.ops_telemetry_url.strip()
        and config.ops_telemetry_api_key.strip()
        and config.ops_client_slug.strip()
    )
