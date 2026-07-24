"""Stable operation identity and committed-observation replay for effects."""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import HarnessApprovalProposalRow, HarnessEffectOperationRow
from src.harness.capability_runtime import CapabilityObservation


class EffectOperationConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedEffect:
    row_id: int
    operation_id: str
    replay: CapabilityObservation | None = None


class EffectOperationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def prepare(
        self,
        *,
        turn_claim_id: int,
        call_id: str,
        capability_id: str,
        arguments_hash: str,
        proposal: HarnessApprovalProposalRow | None = None,
    ) -> PreparedEffect:
        seed = (
            f"proposal:{proposal.proposal_id}:{capability_id}:{arguments_hash}"
            if proposal is not None
            else f"{turn_claim_id}:{call_id}:{capability_id}:{arguments_hash}"
        )
        operation_id = "eff-" + hashlib.sha256(seed.encode()).hexdigest()[:32]
        row = (
            await self._db.execute(
                select(HarnessEffectOperationRow).where(
                    HarnessEffectOperationRow.operation_id == operation_id
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            if row.capability_id != capability_id or row.arguments_hash != arguments_hash:
                raise EffectOperationConflict("Effect identity is bound to another call")
            replay = CapabilityObservation.model_validate(row.observation) if row.status in {"committed", "failed"} and row.observation else None
            return PreparedEffect(row.id, row.operation_id, replay)
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessEffectOperationRow(
            operation_id=operation_id,
            proposal_id=proposal.id if proposal else None,
            turn_claim_id=turn_claim_id,
            call_id=call_id,
            capability_id=capability_id,
            arguments_hash=arguments_hash,
            status="prepared",
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        self._db.add(row)
        await self._db.flush()
        return PreparedEffect(row.id, row.operation_id)

    async def commit(self, prepared: PreparedEffect, observation: CapabilityObservation) -> None:
        row = await self._db.get(HarnessEffectOperationRow, prepared.row_id)
        if row is None:
            raise EffectOperationConflict("Prepared effect disappeared")
        if row.status == "committed":
            return
        now = datetime.datetime.now(datetime.UTC)
        row.status = "committed" if observation.succeeded else "failed"
        row.observation = observation.model_dump(mode="json")
        row.receipt_ref = observation.receipt_ref
        row.workflow_ref = observation.workflow_ref
        row.committed_at = now
        row.updated_at = now
        await self._db.flush()


__all__ = ["EffectOperationConflict", "EffectOperationService", "PreparedEffect"]
