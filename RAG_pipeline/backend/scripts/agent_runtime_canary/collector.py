"""Collect content-free canary samples from append-only turn audits."""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import HarnessAgentTurnAuditRow

from .models import CanaryEvidenceInputs, CanaryTurnSample, CanaryWindowEvidence

MAX_CANARY_AUDITS = 100_000


def sample_from_audit(row: HarnessAgentTurnAuditRow) -> CanaryTurnSample:
    if row.route != "agent":
        raise ValueError(f"audit {row.id} is not an authenticated agent-route audit")
    flags = row.flags if isinstance(row.flags, dict) else {}
    stage = str(flags.get("canary_stage") or "")
    cohort = str(flags.get("runtime_cohort") or "")
    if not stage or not cohort:
        raise ValueError(
            f"audit {row.id} predates required canary stage/cohort attribution"
        )
    return CanaryTurnSample(
        audit_id=row.id,
        canary_stage=stage,
        runtime_cohort=cohort,
        transport=row.transport,
        terminal_action=row.terminal_action,
        stop_reason=row.stop_reason,
        capability_ids=list(row.capability_ids or [])[:12],
        observation_statuses=list(row.observation_statuses or [])[:12],
        repeated=bool(flags.get("repeat")),
        clarified=bool(flags.get("clarification")),
        denied=bool(flags.get("denied")),
        stale_workflow=bool(flags.get("stale_workflow")),
        state_conflict=bool(flags.get("state_conflict")),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        tool_calls=row.tool_calls,
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        observed_at=row.created_at,
    )


async def collect_canary_window(
    db: AsyncSession,
    *,
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
    inputs: CanaryEvidenceInputs,
) -> CanaryWindowEvidence:
    if started_at.tzinfo is None or ended_at.tzinfo is None:
        raise ValueError("canary window timestamps must be timezone-aware")
    if ended_at <= started_at:
        raise ValueError("canary window end must follow its start")
    rows = (
        await db.execute(
            select(HarnessAgentTurnAuditRow)
            .where(
                HarnessAgentTurnAuditRow.created_at >= started_at,
                HarnessAgentTurnAuditRow.created_at <= ended_at,
            )
            .order_by(HarnessAgentTurnAuditRow.created_at, HarnessAgentTurnAuditRow.id)
            .limit(MAX_CANARY_AUDITS + 1)
        )
    ).scalars().all()
    if len(rows) > MAX_CANARY_AUDITS:
        raise ValueError(
            f"canary window exceeds the {MAX_CANARY_AUDITS} audit collection limit"
        )
    return CanaryWindowEvidence(
        **inputs.model_dump(),
        samples=[sample_from_audit(row) for row in rows],
    )


__all__ = ["MAX_CANARY_AUDITS", "collect_canary_window", "sample_from_audit"]
