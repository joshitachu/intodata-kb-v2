"""Durable exact-call approval authority for the effect-primary runtime."""

from __future__ import annotations

import datetime
import hashlib
import json
import secrets
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import HarnessApprovalProposalRow
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.models import CapabilityDefinition


class ApprovalError(ValueError):
    pass


class ApprovalConflict(ApprovalError):
    pass


class ApprovalDenied(ApprovalError):
    pass


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    conversation_id: int
    message_id: int
    capability_id: str
    semantic_name: str
    arguments_hash: str
    risk_tier: str
    target_version: int | None
    status: str
    version: int
    allowed_decisions: list[str]
    expires_at: datetime.datetime


def canonical_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return json.loads(encoded), hashlib.sha256(encoded.encode()).hexdigest()


class ApprovalService:
    def __init__(self, db: AsyncSession, *, ttl_minutes: int = 30) -> None:
        self._db = db
        self._ttl = datetime.timedelta(minutes=max(1, ttl_minutes))

    async def propose(
        self,
        *,
        capability: CapabilityDefinition,
        semantic_name: str,
        arguments: dict[str, Any],
        context: CapabilityExecutionContext,
        call_id: str,
    ) -> ApprovalProjection:
        if context.conversation_id is None or context.current_message_id is None or context.conversation_turn_claim_id is None:
            raise ApprovalDenied("A live conversation turn claim is required")
        canonical, arguments_hash = canonical_arguments(arguments)
        operation_key = f"claim:{context.conversation_turn_claim_id}:call:{call_id}"
        existing = (
            await self._db.execute(
                select(HarnessApprovalProposalRow).where(
                    HarnessApprovalProposalRow.operation_key == operation_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.capability_id != capability.id or existing.arguments_hash != arguments_hash:
                raise ApprovalConflict("The proposal identity is already bound to different arguments")
            return self._projection(existing)
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessApprovalProposalRow(
            proposal_id=f"apr-{secrets.token_urlsafe(12)}",
            conversation_id=context.conversation_id,
            owner_user_id=context.user_id,
            message_id=context.current_message_id,
            turn_claim_id=context.conversation_turn_claim_id,
            operation_key=operation_key,
            capability_id=capability.id,
            semantic_name=semantic_name,
            arguments=canonical,
            arguments_hash=arguments_hash,
            project_scope=sorted(context.project_scope or []),
            required_permissions=sorted(capability.required_permissions),
            risk_tier=capability.risk_tier.value,
            target_version=_target_version(canonical),
            allowed_decisions=[ApprovalDecision.APPROVE.value, ApprovalDecision.REJECT.value],
            status="pending",
            version=1,
            expires_at=now + self._ttl,
            created_at=now,
            updated_at=now,
        )
        self._db.add(row)
        await self._db.flush()
        # The proposal is interrupt authority. Commit it before the gateway
        # returns the approval-required ToolMessage to the runtime/model.
        await self._db.commit()
        return self._projection(row)

    async def pending_for_conversation(self, *, conversation_id: int, owner_user_id: int) -> ApprovalProjection | None:
        now = datetime.datetime.now(datetime.UTC)
        row = (
            await self._db.execute(
                select(HarnessApprovalProposalRow)
                .where(
                    HarnessApprovalProposalRow.conversation_id == conversation_id,
                    HarnessApprovalProposalRow.owner_user_id == owner_user_id,
                    HarnessApprovalProposalRow.status == "pending",
                    HarnessApprovalProposalRow.expires_at > now,
                )
                .order_by(HarnessApprovalProposalRow.id.desc())
            )
        ).scalars().first()
        return self._projection(row) if row else None

    async def decide(
        self,
        *,
        proposal_id: str,
        owner_user_id: int,
        expected_version: int,
        decision: ApprovalDecision,
        project_scope: list[str] | None,
        permissions: set[str] | None,
    ) -> ApprovalProjection:
        row = await self._owned(proposal_id, owner_user_id)
        now = datetime.datetime.now(datetime.UTC)
        if row.status != "pending" or row.version != expected_version:
            raise ApprovalConflict("Approval proposal changed; reload before deciding")
        if _as_utc(row.expires_at) <= now:
            await self._set_expired(row, expected_version, now)
            raise ApprovalConflict("Approval proposal expired")
        if decision is ApprovalDecision.APPROVE:
            if project_scope is not None and not set(row.project_scope or []).issubset(set(project_scope)):
                raise ApprovalDenied("Approval scope is no longer authorized")
            if permissions is not None and not set(row.required_permissions or []).issubset(permissions):
                raise ApprovalDenied("Approval permission is no longer authorized")
        result = await self._db.execute(
            update(HarnessApprovalProposalRow)
            .where(
                HarnessApprovalProposalRow.id == row.id,
                HarnessApprovalProposalRow.status == "pending",
                HarnessApprovalProposalRow.version == expected_version,
            )
            .values(
                status="approved" if decision is ApprovalDecision.APPROVE else "rejected",
                decision=decision.value,
                decided_by_user_id=owner_user_id,
                decided_at=now,
                version=expected_version + 1,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ApprovalConflict("Approval proposal changed; reload before deciding")
        await self._db.flush()
        refreshed = await self._owned(proposal_id, owner_user_id)
        return self._projection(refreshed)

    async def approved_row(self, *, proposal_id: str, owner_user_id: int) -> HarnessApprovalProposalRow:
        row = await self._owned(proposal_id, owner_user_id)
        if row.status != "approved" or row.decision != ApprovalDecision.APPROVE.value:
            raise ApprovalDenied("The exact proposal is not approved")
        if _as_utc(row.expires_at) <= datetime.datetime.now(datetime.UTC):
            raise ApprovalConflict("Approval proposal expired")
        return row

    async def mark_executed(self, *, proposal_id: str, owner_user_id: int, expected_status: str = "approved") -> None:
        row = await self._owned(proposal_id, owner_user_id)
        if row.status == "executed":
            return
        if row.status != expected_status:
            raise ApprovalConflict("Approval proposal is not executable")
        row.status = "executed"
        row.version += 1
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await self._db.flush()

    async def _owned(self, proposal_id: str, owner_user_id: int) -> HarnessApprovalProposalRow:
        row = (
            await self._db.execute(
                select(HarnessApprovalProposalRow).where(
                    HarnessApprovalProposalRow.proposal_id == proposal_id,
                    HarnessApprovalProposalRow.owner_user_id == owner_user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApprovalDenied("Approval proposal not found")
        return row

    async def _set_expired(self, row: HarnessApprovalProposalRow, version: int, now: datetime.datetime) -> None:
        await self._db.execute(
            update(HarnessApprovalProposalRow)
            .where(HarnessApprovalProposalRow.id == row.id, HarnessApprovalProposalRow.version == version)
            .values(status="expired", version=version + 1, updated_at=now)
        )

    @staticmethod
    def _projection(row: HarnessApprovalProposalRow) -> ApprovalProjection:
        return ApprovalProjection(
            proposal_id=row.proposal_id,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            capability_id=row.capability_id,
            semantic_name=row.semantic_name,
            arguments_hash=row.arguments_hash,
            risk_tier=row.risk_tier,
            target_version=row.target_version,
            status=row.status,
            version=row.version,
            allowed_decisions=list(row.allowed_decisions or []),
            expires_at=_as_utc(row.expires_at),
        )


def _target_version(arguments: dict[str, Any]) -> int | None:
    value = arguments.get("expected_version", arguments.get("expected_revision"))
    return value if isinstance(value, int) and value > 0 else None


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value.astimezone(datetime.UTC)


__all__ = [
    "ApprovalConflict",
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalError",
    "ApprovalProjection",
    "ApprovalService",
    "canonical_arguments",
]
