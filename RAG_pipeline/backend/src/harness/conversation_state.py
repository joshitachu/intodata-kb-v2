"""Authoritative conversational focus, task references, and turn claims.

Conversation state never owns Pack or workflow lifecycle truth.  It stores
validated pointers to those authorities and uses a durable claim before any
stateful capability may execute.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import secrets
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.conversation import Conversation, ConversationMessage
from src.db.models.harness import (
    HarnessConversationStateRow,
    HarnessConversationStateTransitionRow,
    HarnessConversationTurnClaimRow,
    HarnessWorkflowInterruptRow,
    HarnessWorkflowRunRow,
)
from src.db.models.harness_pack import HarnessPackDraftRow


_CLAIM_SECONDS = 90
_MAX_FINGERPRINTS = 20
_MAX_EFFECT_RECEIPTS = 12
_MAX_FINAL_RESULT_CHARS = 16_000
_MAX_PREPARED_ACTION_BYTES = 32_000
_ACTIVE_CLAIM_STATUSES = frozenset(
    {"claimed", "effect_started", "recovery_required"}
)
_NONTERMINAL_WORKFLOW_STATUSES = frozenset(
    {"pending", "running", "triggered", "interrupted", "indeterminate"}
)


class ConversationStateError(RuntimeError):
    """Base error for state/claim failures."""


class ConversationStateConflict(ConversationStateError):
    """A stale version or competing active turn owns the state."""


class ConversationStateDenied(ConversationStateError):
    """A claim, message, or task reference is not authorized."""


class ConversationTaskKind(StrEnum):
    PACK_DRAFT = "pack_draft"
    ONBOARDING_WORKFLOW = "onboarding_workflow"
    DOCUMENTATION_WORKFLOW = "documentation_workflow"


class ConversationTaskFocus(StrEnum):
    FOCUSED = "focused"
    INACTIVE = "inactive"
    BACKGROUND = "background"
    PAUSED = "paused"


class TurnClaimStatus(StrEnum):
    CLAIMED = "claimed"
    EFFECT_STARTED = "effect_started"
    APPLIED = "applied"
    FAILED = "failed"
    ABANDONED = "abandoned"
    RECOVERY_REQUIRED = "recovery_required"


class StateTransitionSource(StrEnum):
    VALIDATED_AGENT_ACTION = "validated_agent_action"
    WORKFLOW_OUTCOME = "workflow_outcome"
    MIGRATION_BACKFILL = "migration_backfill"


class ConversationEntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(min_length=1, max_length=40)
    stable_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    project_id: str | None = Field(default=None, max_length=255)


class ConversationRequestedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ref_id: str | None = Field(default=None, min_length=1, max_length=255)
    field: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=20)


class PreparedActionValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: list[str] = Field(default_factory=list, max_length=12)
    code: str = Field(min_length=1, max_length=40)
    message: str = Field(min_length=1, max_length=240)


class ConversationPreparedAction(BaseModel):
    """Bounded cross-turn preparation context, never execution authority."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(min_length=1, max_length=160)
    semantic_name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    arguments: dict[str, Any] = Field(default_factory=dict)
    missing_user_information: list[str] = Field(default_factory=list, max_length=8)
    validation_errors: list[PreparedActionValidationError] = Field(
        default_factory=list, max_length=8
    )
    source_message_ids: list[int] = Field(default_factory=list, max_length=12)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def bounded_json_payload(self) -> "ConversationPreparedAction":
        try:
            encoded = json.dumps(
                self.arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("prepared action arguments must be JSON serializable") from exc
        if len(encoded) > _MAX_PREPARED_ACTION_BYTES:
            raise ValueError("prepared action arguments exceed the bounded size")
        if any(message_id < 1 for message_id in self.source_message_ids):
            raise ValueError("prepared action source message ids must be positive")
        return self

    @property
    def ready(self) -> bool:
        return not self.missing_user_information and not self.validation_errors


class ConversationTaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ref_id: str = Field(min_length=1, max_length=255)
    kind: ConversationTaskKind
    capability_id: str = Field(min_length=1, max_length=160)
    draft_id: int | None = Field(default=None, ge=1)
    workflow_run_id: int | None = Field(default=None, ge=1)
    project_id: str | None = Field(default=None, max_length=255)
    focused: bool = False
    focus_state: ConversationTaskFocus = ConversationTaskFocus.INACTIVE

    @model_validator(mode="after")
    def exact_authority_pointer(self) -> "ConversationTaskReference":
        if self.focused and self.focus_state is ConversationTaskFocus.INACTIVE:
            self.focus_state = ConversationTaskFocus.FOCUSED
        self.focused = self.focus_state is ConversationTaskFocus.FOCUSED
        if self.kind is ConversationTaskKind.PACK_DRAFT:
            if self.draft_id is None or self.workflow_run_id is not None:
                raise ValueError("Pack task requires only draft_id")
        elif self.workflow_run_id is None or self.draft_id is not None:
            raise ValueError("Workflow task requires only workflow_run_id")
        return self


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    conversation_id: int
    owner_user_id: int
    version: int = Field(ge=1)
    objective: str | None = Field(default=None, max_length=2000)
    referenced_entities: list[ConversationEntityReference] = Field(
        default_factory=list, max_length=30
    )
    requested_inputs: list[ConversationRequestedInput] = Field(
        default_factory=list, max_length=30
    )
    active_tasks: list[ConversationTaskReference] = Field(
        default_factory=list, max_length=30
    )
    prepared_action: ConversationPreparedAction | None = None
    response_fingerprints: list[str] = Field(default_factory=list, max_length=20)
    last_applied_message_id: int | None = None

    @model_validator(mode="after")
    def one_focused_task(self) -> "ConversationState":
        if sum(1 for item in self.active_tasks if item.focused) > 1:
            raise ValueError("Conversation State may have at most one focused task")
        task_ids = {item.task_ref_id for item in self.active_tasks}
        if len(task_ids) != len(self.active_tasks):
            raise ValueError("Conversation task references must be unique")
        if any(
            item.task_ref_id is not None and item.task_ref_id not in task_ids
            for item in self.requested_inputs
        ):
            raise ValueError("Requested input must reference an active task")
        return self

    def model_projection(self) -> dict[str, Any]:
        """Return only bounded facts safe for the decision model."""

        return self.model_dump(
            mode="json",
            exclude={"id", "owner_user_id", "response_fingerprints"},
        )


class ConversationTaskEffect(BaseModel):
    """Validated effect emitted by a successful registered task handler."""

    model_config = ConfigDict(extra="forbid")

    call_key: str = Field(min_length=1, max_length=255)
    capability_id: str = Field(min_length=1, max_length=160)
    task_ref: ConversationTaskReference | None = None
    remove_task_ref_id: str | None = Field(default=None, max_length=255)
    objective: str | None = Field(default=None, max_length=2000)
    referenced_entities: list[ConversationEntityReference] = Field(
        default_factory=list, max_length=10
    )
    requested_inputs: list[ConversationRequestedInput] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def has_one_effect(self) -> "ConversationTaskEffect":
        if self.task_ref is None and self.remove_task_ref_id is None:
            raise ValueError("Task effect requires a task reference or removal")
        if self.task_ref is not None and self.remove_task_ref_id is not None:
            raise ValueError("Task effect cannot add and remove simultaneously")
        return self


class ConversationEffectReceipt(BaseModel):
    """Exact completed call receipt returned instead of redispatching a tool."""

    model_config = ConfigDict(extra="forbid")

    effect: ConversationTaskEffect
    domain_ref: dict[str, Any] = Field(default_factory=dict)


class ConversationTurnClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    conversation_state_id: int
    message_id: int
    expected_version: int
    idempotency_key: str
    status: TurnClaimStatus
    lease_token: str | None = Field(default=None, exclude=True)
    lease_expires_at: datetime.datetime
    effects: list[ConversationTaskEffect] = Field(default_factory=list)
    final_result: dict[str, Any] | None = None

    @property
    def replayed(self) -> bool:
        return self.status in {TurnClaimStatus.APPLIED, TurnClaimStatus.FAILED}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ConversationStateRepository:
    """Single mutation authority for Conversation State and turn leases."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_or_create(
        self, *, conversation_id: int, owner_user_id: int
    ) -> ConversationState:
        await self._require_conversation_owner(conversation_id, owner_user_id)
        row = await self._state_row(conversation_id, owner_user_id)
        if row is None:
            now = _now()
            row = HarnessConversationStateRow(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                version=1,
                objective=None,
                referenced_entities=[],
                requested_inputs=[],
                active_tasks=[],
                prepared_action=None,
                response_fingerprints=[],
                last_applied_message_id=None,
                created_at=now,
                updated_at=now,
            )
            self._db.add(row)
            try:
                await self._db.flush()
            except IntegrityError as exc:
                raise ConversationStateConflict(
                    "Conversation State was created concurrently; reload and retry"
                ) from exc
        return _to_state(row)

    async def load(
        self, *, conversation_id: int, owner_user_id: int
    ) -> ConversationState:
        await self._require_conversation_owner(conversation_id, owner_user_id)
        row = await self._state_row(conversation_id, owner_user_id)
        if row is None:
            return await self.get_or_create(
                conversation_id=conversation_id, owner_user_id=owner_user_id
            )
        return _to_state(row)

    async def claim_turn(
        self,
        *,
        conversation_id: int,
        owner_user_id: int,
        message_id: int,
        expected_version: int,
        idempotency_key: str,
        lease_seconds: int = _CLAIM_SECONDS,
        now: datetime.datetime | None = None,
    ) -> ConversationTurnClaim:
        """Reserve the state before any stateful capability can run.

        Callers must commit this short transaction before model execution.
        """

        timestamp = now or _now()
        state = await self.load(
            conversation_id=conversation_id, owner_user_id=owner_user_id
        )
        message = await self._db.get(ConversationMessage, message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.role != "user"
        ):
            raise ConversationStateDenied("Turn claim requires the exact user message")
        existing_message = (
            await self._db.execute(
                select(HarnessConversationTurnClaimRow).where(
                    HarnessConversationTurnClaimRow.message_id == message_id
                )
            )
        ).scalar_one_or_none()
        if existing_message is not None:
            if existing_message.idempotency_key != idempotency_key:
                raise ConversationStateConflict("Message already has a different turn claim")
            return await self._replay_or_reclaim(existing_message, timestamp, lease_seconds)
        if state.version != expected_version:
            raise ConversationStateConflict("Conversation State version changed; reload")

        active = (
            await self._db.execute(
                select(HarnessConversationTurnClaimRow)
                .where(
                    HarnessConversationTurnClaimRow.conversation_state_id == state.id,
                    HarnessConversationTurnClaimRow.status.in_(_ACTIVE_CLAIM_STATUSES),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if active is not None:
            await self._recover_expired_active(active, timestamp)
            if active.status in _ACTIVE_CLAIM_STATUSES:
                raise ConversationStateConflict("Another message is applying Conversation State")

        token = secrets.token_urlsafe(32)
        row = HarnessConversationTurnClaimRow(
            conversation_state_id=state.id,
            message_id=message_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key[:255],
            lease_token_hash=_token_hash(token),
            status=TurnClaimStatus.CLAIMED.value,
            lease_expires_at=timestamp + datetime.timedelta(seconds=lease_seconds),
            effect_receipts=[],
            final_result=None,
            applied_transition_id=None,
            claimed_at=timestamp,
            effect_started_at=None,
            finalized_at=None,
        )
        self._db.add(row)
        try:
            await self._db.flush()
        except IntegrityError as exc:
            raise ConversationStateConflict("Another turn claimed Conversation State") from exc
        return _to_claim(row, lease_token=token)

    async def start_effect(
        self,
        *,
        claim_id: int,
        lease_token: str,
        call_key: str,
        capability_id: str,
        authority_hint: dict[str, Any] | None = None,
        now: datetime.datetime | None = None,
    ) -> ConversationEffectReceipt | None:
        """Renew a valid claim immediately before a stateful domain effect.

        Returns a previously recorded successful effect for idempotent replay.
        """

        timestamp = now or _now()
        row = await self._require_claim(claim_id, lease_token, timestamp)
        receipts = [dict(item) for item in row.effect_receipts or []]
        for item in receipts:
            if item.get("call_key") != call_key:
                continue
            if item.get("capability_id") != capability_id:
                raise ConversationStateDenied(
                    "Capability call key is already bound to another capability"
                )
            effect = item.get("effect")
            if isinstance(effect, dict):
                return ConversationEffectReceipt(
                    effect=ConversationTaskEffect.model_validate(effect),
                    domain_ref=_bounded_mapping(
                        item.get("domain_ref")
                        if isinstance(item.get("domain_ref"), dict)
                        else {}
                    ),
                )
            if item.get("phase") == "started":
                raise ConversationStateConflict("Capability effect is already in progress")
        if len(receipts) >= _MAX_EFFECT_RECEIPTS:
            raise ConversationStateDenied("Turn effect receipt limit exceeded")
        receipts.append(
            {
                "call_key": call_key[:255],
                "capability_id": capability_id[:160],
                "phase": "started",
                "started_at": timestamp.isoformat(),
                "authority_hint": _bounded_mapping(authority_hint or {}),
            }
        )
        row.effect_receipts = receipts
        row.status = TurnClaimStatus.EFFECT_STARTED.value
        row.effect_started_at = row.effect_started_at or timestamp
        row.lease_expires_at = timestamp + datetime.timedelta(seconds=_CLAIM_SECONDS)
        await self._db.flush()
        return None

    async def validate_claim(
        self, *, claim_id: int, lease_token: str, now: datetime.datetime | None = None
    ) -> ConversationTurnClaim:
        """Public validation seam for bounded aggregate services.

        AgentPlan uses this before changing its own rows; it deliberately does
        not start a domain effect or write an effect receipt.
        """
        return _to_claim(await self._require_claim(claim_id, lease_token, now or _now()), lease_token=lease_token)

    async def abort_effect(
        self,
        *,
        claim_id: int,
        lease_token: str,
        call_key: str,
        capability_id: str,
        reason: str,
        now: datetime.datetime | None = None,
    ) -> None:
        """Close a claimed call that deterministically performed no domain effect."""

        timestamp = now or _now()
        row = await self._require_claim(claim_id, lease_token, timestamp)
        receipts = [dict(item) for item in row.effect_receipts or []]
        for item in receipts:
            if item.get("call_key") != call_key:
                continue
            if item.get("capability_id") != capability_id:
                raise ConversationStateDenied(
                    "Capability call key is bound to another capability"
                )
            if item.get("phase") == "completed":
                raise ConversationStateConflict("Completed capability effect cannot be aborted")
            item["phase"] = "aborted"
            item["abort_reason"] = " ".join(reason.split())[:240]
            item["completed_at"] = timestamp.isoformat()
            row.effect_receipts = receipts
            if not any(entry.get("phase") == "started" for entry in receipts):
                row.status = TurnClaimStatus.CLAIMED.value
            row.lease_expires_at = timestamp + datetime.timedelta(seconds=_CLAIM_SECONDS)
            await self._db.flush()
            return
        raise ConversationStateDenied("Capability effect was not claimed before abort")

    async def record_effect(
        self,
        *,
        claim_id: int,
        lease_token: str,
        effect: ConversationTaskEffect,
        domain_ref: dict[str, Any] | None = None,
        now: datetime.datetime | None = None,
    ) -> None:
        timestamp = now or _now()
        row = await self._require_claim(claim_id, lease_token, timestamp)
        receipts = [dict(item) for item in row.effect_receipts or []]
        matched = False
        for item in receipts:
            if item.get("call_key") == effect.call_key:
                if item.get("capability_id") != effect.capability_id:
                    raise ConversationStateDenied(
                        "Capability effect does not match its claimed call"
                    )
                if item.get("phase") != "started":
                    raise ConversationStateConflict(
                        "Capability effect receipt is not awaiting completion"
                    )
                item["phase"] = "completed"
                item["effect"] = effect.model_dump(mode="json")
                item["domain_ref"] = _bounded_mapping(domain_ref or {})
                item["completed_at"] = timestamp.isoformat()
                matched = True
                break
        if not matched:
            raise ConversationStateDenied("Capability effect was not claimed before execution")
        row.effect_receipts = receipts
        row.lease_expires_at = timestamp + datetime.timedelta(seconds=_CLAIM_SECONDS)
        await self._db.flush()

    async def set_effect_authority(
        self,
        *,
        claim_id: int,
        lease_token: str,
        call_key: str,
        authority_hint: dict[str, Any],
        now: datetime.datetime | None = None,
    ) -> None:
        """Persist an exact domain identity before an external provider call."""

        timestamp = now or _now()
        row = await self._require_claim(claim_id, lease_token, timestamp)
        receipts = [dict(item) for item in row.effect_receipts or []]
        for item in receipts:
            if item.get("call_key") == call_key and item.get("phase") == "started":
                item["authority_hint"] = _bounded_mapping(authority_hint)
                row.effect_receipts = receipts
                row.lease_expires_at = timestamp + datetime.timedelta(
                    seconds=_CLAIM_SECONDS
                )
                await self._db.flush()
                return
        raise ConversationStateDenied("Capability effect is not awaiting authority")

    async def finalize_claim(
        self,
        *,
        claim_id: int,
        lease_token: str,
        action: str,
        final_result: dict[str, Any],
        response_fingerprint: str | None = None,
        focus_task_ref_id: str | None = None,
        complete_objective: bool = False,
        prepared_action: ConversationPreparedAction | None = None,
        clear_prepared_action: bool = False,
        now: datetime.datetime | None = None,
    ) -> ConversationState:
        timestamp = now or _now()
        claim = await self._require_claim(claim_id, lease_token, timestamp)
        state_row = await self._db.get(HarnessConversationStateRow, claim.conversation_state_id)
        if state_row is None:
            raise ConversationStateDenied("Conversation State is unavailable")
        if state_row.version != claim.expected_version:
            await self._fail_row(
                claim,
                {"stop_reason": "state_conflict", "output": "Conversation State changed."},
                timestamp,
            )
            raise ConversationStateConflict("Conversation State changed before finalization")

        effects = _completed_effects(claim.effect_receipts or [])
        current_state = _to_state(state_row)
        allow_objective_completion = (
            complete_objective
            and not current_state.active_tasks
            and any(
                item.task_ref_id is None for item in current_state.requested_inputs
            )
        )
        state = _apply_effects(
            current_state,
            effects,
            response_fingerprint,
            focus_task_ref_id=focus_task_ref_id,
            complete_objective=allow_objective_completion,
        )
        if prepared_action is not None and clear_prepared_action:
            raise ValueError("prepared action cannot be set and cleared together")
        next_prepared_action = (
            None
            if clear_prepared_action
            else prepared_action or current_state.prepared_action
        )
        state = state.model_copy(update={"prepared_action": next_prepared_action})
        next_version = state_row.version + 1
        result = await self._db.execute(
            update(HarnessConversationStateRow)
            .where(
                HarnessConversationStateRow.id == state_row.id,
                HarnessConversationStateRow.version == claim.expected_version,
            )
            .values(
                version=next_version,
                objective=state.objective,
                referenced_entities=[
                    item.model_dump(mode="json") for item in state.referenced_entities
                ],
                requested_inputs=[
                    item.model_dump(mode="json") for item in state.requested_inputs
                ],
                active_tasks=[item.model_dump(mode="json") for item in state.active_tasks],
                prepared_action=(
                    state.prepared_action.model_dump(mode="json")
                    if state.prepared_action is not None
                    else None
                ),
                response_fingerprints=list(state.response_fingerprints),
                last_applied_message_id=claim.message_id,
                updated_at=timestamp,
            )
        )
        if result.rowcount != 1:
            raise ConversationStateConflict("Conversation State update lost its version race")
        transition = HarnessConversationStateTransitionRow(
            conversation_state_id=state_row.id,
            turn_claim_id=claim.id,
            message_id=claim.message_id,
            transition_key=f"claim:{claim.id}:apply",
            from_version=claim.expected_version,
            to_version=next_version,
            source=StateTransitionSource.VALIDATED_AGENT_ACTION.value,
            action=action[:80],
            change_summary={
                "effect_count": len(effects),
                "task_refs": [
                    item.task_ref.task_ref_id
                    for item in effects
                    if item.task_ref is not None
                ],
                "prepared_action": (
                    "cleared"
                    if clear_prepared_action
                    else "set"
                    if prepared_action is not None
                    else "preserved"
                ),
            },
            created_at=timestamp,
        )
        self._db.add(transition)
        await self._db.flush()
        claim.status = TurnClaimStatus.APPLIED.value
        normalized_result = dict(final_result)
        if "complete_objective" in normalized_result:
            normalized_result["complete_objective"] = allow_objective_completion
        claim.final_result = _bounded_result(normalized_result)
        claim.applied_transition_id = transition.id
        claim.finalized_at = timestamp
        await self._db.flush()
        refreshed = await self._db.get(HarnessConversationStateRow, state_row.id)
        assert refreshed is not None
        return _to_state(refreshed)

    async def fail_claim(
        self,
        *,
        claim_id: int,
        lease_token: str,
        final_result: dict[str, Any],
        recovery_required: bool = False,
        now: datetime.datetime | None = None,
    ) -> None:
        timestamp = now or _now()
        claim = await self._require_claim(
            claim_id,
            lease_token,
            timestamp,
            allow_expired=recovery_required,
            check_state_version=False,
        )
        if _completed_effects(claim.effect_receipts or []) and not recovery_required:
            raise ConversationStateConflict(
                "Validated effects must be finalized before releasing the claim"
            )
        if any(item.get("phase") == "started" for item in claim.effect_receipts or []):
            recovery_required = True
        claim.status = (
            TurnClaimStatus.RECOVERY_REQUIRED.value
            if recovery_required
            else TurnClaimStatus.FAILED.value
        )
        claim.final_result = _bounded_result(final_result)
        claim.finalized_at = None if recovery_required else timestamp
        await self._db.flush()

    async def reconcile_authorities(
        self,
        *,
        conversation_id: int,
        owner_user_id: int,
        now: datetime.datetime | None = None,
    ) -> ConversationState:
        """Drop terminal pointers and rebuild requested inputs from exact truth."""

        timestamp = now or _now()
        row = await self._state_row(conversation_id, owner_user_id)
        if row is None:
            return await self.get_or_create(
                conversation_id=conversation_id, owner_user_id=owner_user_id
            )
        state = _to_state(row)
        active: list[ConversationTaskReference] = []
        requested: list[ConversationRequestedInput] = [
            item for item in state.requested_inputs if item.task_ref_id is None
        ]
        for task in state.active_tasks:
            if task.kind is ConversationTaskKind.PACK_DRAFT:
                draft = await self._db.get(HarnessPackDraftRow, task.draft_id)
                if (
                    draft is None
                    or draft.owner_user_id != owner_user_id
                    or draft.conversation_id not in {None, conversation_id}
                    or draft.status != "active"
                ):
                    continue
                canonical = task.model_copy(
                    update={
                        "task_ref_id": f"pack:{draft.id}",
                        "kind": ConversationTaskKind.PACK_DRAFT,
                        "draft_id": draft.id,
                        "workflow_run_id": None,
                        "project_id": None,
                    }
                )
                active.append(canonical)
                requested.extend(_pack_requested_inputs(canonical, draft.validation))
                continue
            workflow = await self._db.get(HarnessWorkflowRunRow, task.workflow_run_id)
            if (
                workflow is None
                or workflow.user_id != owner_user_id
                or workflow.conversation_id != conversation_id
                or workflow.status not in _NONTERMINAL_WORKFLOW_STATUSES
            ):
                continue
            canonical_kind = (
                ConversationTaskKind.ONBOARDING_WORKFLOW
                if workflow.capability_id == "nexus.workflow.onboard_project"
                else ConversationTaskKind.DOCUMENTATION_WORKFLOW
            )
            canonical = task.model_copy(
                update={
                    "task_ref_id": f"workflow:{workflow.id}",
                    "kind": canonical_kind,
                    "capability_id": workflow.capability_id,
                    "workflow_run_id": workflow.id,
                    "draft_id": None,
                    "project_id": workflow.project_id,
                }
            )
            active.append(canonical)
            requested.extend(await self._workflow_requested_inputs(canonical, workflow.id))
        active = _normalize_focus(active)
        valid_task_ids = {item.task_ref_id for item in active}
        requested = [
            item
            for item in requested
            if item.task_ref_id is None or item.task_ref_id in valid_task_ids
        ]
        target_objective = state.objective if (active or requested) else None
        if (
            [item.model_dump(mode="json") for item in active] == row.active_tasks
            and [item.model_dump(mode="json") for item in requested] == row.requested_inputs
            and target_objective == row.objective
        ):
            return state

        # Authority reconciliation never races an active user turn.
        active_claim = (
            await self._db.execute(
                select(HarnessConversationTurnClaimRow).where(
                    HarnessConversationTurnClaimRow.conversation_state_id == row.id,
                    HarnessConversationTurnClaimRow.status.in_(_ACTIVE_CLAIM_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if active_claim is not None:
            raise ConversationStateConflict("A turn claim is active during reconciliation")
        prior_version = row.version
        row.version += 1
        row.active_tasks = [item.model_dump(mode="json") for item in active]
        row.requested_inputs = [item.model_dump(mode="json") for item in requested]
        row.objective = target_objective
        row.updated_at = timestamp
        self._db.add(
            HarnessConversationStateTransitionRow(
                conversation_state_id=row.id,
                turn_claim_id=None,
                message_id=None,
                transition_key=f"authority:{row.id}:{prior_version}:{row.version}",
                from_version=prior_version,
                to_version=row.version,
                source=StateTransitionSource.WORKFLOW_OUTCOME.value,
                action="reconcile_authorities",
                change_summary={
                    "active_task_count": len(active),
                    "requested_input_count": len(requested),
                },
                created_at=timestamp,
            )
        )
        await self._db.flush()
        return _to_state(row)

    async def recover_claim_authority(
        self,
        *,
        claim_id: int,
        owner_user_id: int,
        now: datetime.datetime | None = None,
    ) -> ConversationState | None:
        """Resolve an expired started call from exact persisted domain truth.

        ``None`` means the authority remains genuinely ambiguous and the
        recovery hold must stay active. No domain operation is redispatched.
        """

        timestamp = now or _now()
        claim = await self._db.get(HarnessConversationTurnClaimRow, claim_id)
        if claim is None:
            raise ConversationStateDenied("Turn claim is unavailable")
        state_row = await self._db.get(
            HarnessConversationStateRow, claim.conversation_state_id
        )
        if state_row is None or state_row.owner_user_id != owner_user_id:
            raise ConversationStateDenied("Turn claim is unavailable")
        if claim.status not in {
            TurnClaimStatus.EFFECT_STARTED.value,
            TurnClaimStatus.RECOVERY_REQUIRED.value,
        }:
            raise ConversationStateConflict("Turn claim is not awaiting recovery")
        if (
            claim.status == TurnClaimStatus.EFFECT_STARTED.value
            and _as_utc(claim.lease_expires_at) > timestamp
        ):
            raise ConversationStateConflict("Turn claim lease is still active")

        receipts = [dict(item) for item in claim.effect_receipts or []]
        ambiguous = False
        for item in receipts:
            if item.get("phase") != "started":
                continue
            hint = item.get("authority_hint")
            hint = hint if isinstance(hint, dict) else {}
            kind = hint.get("kind")
            capability_id = str(item.get("capability_id") or "")
            call_key = str(item.get("call_key") or "")
            effect: ConversationTaskEffect | None = None
            domain_ref: dict[str, Any] = {}

            if kind == "pack_revision" and isinstance(hint.get("draft_id"), int):
                draft = await self._db.get(HarnessPackDraftRow, hint["draft_id"])
                if (
                    draft is None
                    or draft.owner_user_id != owner_user_id
                    or draft.conversation_id != state_row.conversation_id
                ):
                    ambiguous = True
                    continue
                expected = hint.get("expected_revision")
                target = hint.get("target_revision")
                if draft.revision == expected and target != expected:
                    item["phase"] = "aborted"
                    item["abort_reason"] = "Exact Pack revision proves no mutation committed"
                    item["completed_at"] = timestamp.isoformat()
                    continue
                if draft.revision != target:
                    ambiguous = True
                    continue
                if draft.status == "active":
                    task = ConversationTaskReference(
                        task_ref_id=f"pack:{draft.id}",
                        kind=ConversationTaskKind.PACK_DRAFT,
                        capability_id=capability_id,
                        draft_id=draft.id,
                        focused=True,
                    )
                    effect = ConversationTaskEffect(
                        call_key=call_key,
                        capability_id=capability_id,
                        task_ref=task,
                        requested_inputs=_pack_requested_inputs(task, draft.validation),
                    )
                elif draft.status in {"saved", "abandoned"}:
                    effect = ConversationTaskEffect(
                        call_key=call_key,
                        capability_id=capability_id,
                        remove_task_ref_id=f"pack:{draft.id}",
                    )
                else:
                    ambiguous = True
                    continue
                domain_ref = {
                    "draft_id": draft.id,
                    "revision": draft.revision,
                    "status": draft.status,
                }
            elif kind == "workflow_run" and isinstance(
                hint.get("workflow_run_id"), int
            ):
                workflow = await self._db.get(
                    HarnessWorkflowRunRow, hint["workflow_run_id"]
                )
                if (
                    workflow is None
                    or workflow.user_id != owner_user_id
                    or workflow.conversation_id != state_row.conversation_id
                ):
                    ambiguous = True
                    continue
                if workflow.status in _NONTERMINAL_WORKFLOW_STATUSES:
                    task_kind = (
                        ConversationTaskKind.ONBOARDING_WORKFLOW
                        if workflow.capability_id == "nexus.workflow.onboard_project"
                        else ConversationTaskKind.DOCUMENTATION_WORKFLOW
                    )
                    effect = ConversationTaskEffect(
                        call_key=call_key,
                        capability_id=capability_id,
                        task_ref=ConversationTaskReference(
                            task_ref_id=f"workflow:{workflow.id}",
                            kind=task_kind,
                            capability_id=workflow.capability_id,
                            workflow_run_id=workflow.id,
                            project_id=workflow.project_id,
                            focused=True,
                        ),
                    )
                else:
                    effect = ConversationTaskEffect(
                        call_key=call_key,
                        capability_id=capability_id,
                        remove_task_ref_id=f"workflow:{workflow.id}",
                    )
                domain_ref = {
                    "workflow_run_id": workflow.id,
                    "project_id": workflow.project_id,
                    "status": workflow.status,
                }
            elif kind in {"pack_create", "workflow_create"}:
                # These writes and their exact identity are one local
                # transaction. Without a committed identity, no provider call
                # passed the durability barrier and no local mutation survived.
                item["phase"] = "aborted"
                item["abort_reason"] = "No committed domain authority exists"
                item["completed_at"] = timestamp.isoformat()
                continue
            else:
                ambiguous = True
                continue

            item["phase"] = "completed"
            item["effect"] = effect.model_dump(mode="json")
            item["domain_ref"] = _bounded_mapping(domain_ref)
            item["completed_at"] = timestamp.isoformat()

        claim.effect_receipts = receipts
        if ambiguous:
            claim.status = TurnClaimStatus.RECOVERY_REQUIRED.value
            await self._db.flush()
            return None

        completed = _completed_effects(receipts)
        if not completed:
            await self._fail_row(
                claim,
                {"stop_reason": "recovered_no_effect", "output": "No domain effect committed."},
                timestamp,
            )
            return _to_state(state_row)

        token = secrets.token_urlsafe(32)
        claim.lease_token_hash = _token_hash(token)
        claim.status = TurnClaimStatus.EFFECT_STARTED.value
        claim.lease_expires_at = timestamp + datetime.timedelta(seconds=_CLAIM_SECONDS)
        await self._db.flush()
        return await self.finalize_claim(
            claim_id=claim.id,
            lease_token=token,
            action="recover_authoritative_effect",
            final_result={
                "stop_reason": "recovered_authoritative_effect",
                "output": "Recovered from exact persisted domain authority.",
            },
            now=timestamp,
        )

    async def _workflow_requested_inputs(
        self, task: ConversationTaskReference, workflow_run_id: int
    ) -> list[ConversationRequestedInput]:
        interrupt = (
            await self._db.execute(
                select(HarnessWorkflowInterruptRow)
                .where(
                    HarnessWorkflowInterruptRow.workflow_run_id == workflow_run_id,
                    HarnessWorkflowInterruptRow.status == "pending",
                )
                .order_by(HarnessWorkflowInterruptRow.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if interrupt is None:
            return []
        fields = []
        scope = interrupt.scope_snapshot or {}
        raw_fields = scope.get("required_fields") if isinstance(scope, dict) else None
        if isinstance(raw_fields, list):
            fields = [str(item)[:80] for item in raw_fields if isinstance(item, str)]
        if not fields:
            fields = [str(interrupt.interrupt_type)[:80]]
        raw_options = interrupt.options or []
        options = [
            str(item.get("value") or item.get("id") or item.get("label"))[:120]
            for item in raw_options
            if isinstance(item, dict)
            and (item.get("value") or item.get("id") or item.get("label"))
        ][:20]
        return [
            ConversationRequestedInput(
                task_ref_id=task.task_ref_id,
                field=field,
                prompt=str(interrupt.prompt)[:500],
                options=options,
            )
            for field in fields[:10]
        ]

    async def _replay_or_reclaim(
        self,
        row: HarnessConversationTurnClaimRow,
        timestamp: datetime.datetime,
        lease_seconds: int,
    ) -> ConversationTurnClaim:
        if row.status in {TurnClaimStatus.APPLIED.value, TurnClaimStatus.FAILED.value}:
            return _to_claim(row)
        safely_reclaimable = (
            row.status
            in {TurnClaimStatus.CLAIMED.value, TurnClaimStatus.ABANDONED.value}
            and not row.effect_receipts
        )
        if (
            row.status != TurnClaimStatus.ABANDONED.value
            and _as_utc(row.lease_expires_at) > timestamp
        ):
            raise ConversationStateConflict("This message turn is still in progress")
        if safely_reclaimable:
            state = await self._db.get(
                HarnessConversationStateRow, row.conversation_state_id
            )
            if state is None or state.version != row.expected_version:
                await self._fail_row(
                    row,
                    {
                        "stop_reason": "state_conflict",
                        "output": "Conversation State changed before this turn resumed.",
                    },
                    timestamp,
                )
                raise ConversationStateConflict(
                    "Expired turn claim has a stale Conversation State version"
                )
            token = secrets.token_urlsafe(32)
            row.lease_token_hash = _token_hash(token)
            row.status = TurnClaimStatus.CLAIMED.value
            row.lease_expires_at = timestamp + datetime.timedelta(seconds=lease_seconds)
            row.claimed_at = timestamp
            row.finalized_at = None
            row.final_result = None
            await self._db.flush()
            return _to_claim(row, lease_token=token)
        row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
        await self._db.flush()
        raise ConversationStateConflict("This message has an effect requiring reconciliation")

    async def _recover_expired_active(
        self, row: HarnessConversationTurnClaimRow, timestamp: datetime.datetime
    ) -> None:
        if _as_utc(row.lease_expires_at) > timestamp:
            return
        if row.status == TurnClaimStatus.CLAIMED.value and not row.effect_receipts:
            row.status = TurnClaimStatus.ABANDONED.value
            row.finalized_at = timestamp
        else:
            row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
        await self._db.flush()

    async def _require_claim(
        self,
        claim_id: int,
        lease_token: str,
        timestamp: datetime.datetime,
        *,
        allow_expired: bool = False,
        check_state_version: bool = True,
    ) -> HarnessConversationTurnClaimRow:
        row = await self._db.get(HarnessConversationTurnClaimRow, claim_id)
        if row is None or not hmac.compare_digest(row.lease_token_hash, _token_hash(lease_token)):
            raise ConversationStateDenied("Turn claim is unavailable")
        if row.status not in _ACTIVE_CLAIM_STATUSES:
            raise ConversationStateConflict("Turn claim is no longer active")
        if not allow_expired and _as_utc(row.lease_expires_at) <= timestamp:
            await self._recover_expired_active(row, timestamp)
            raise ConversationStateConflict("Turn claim expired")
        if check_state_version:
            state = await self._db.get(
                HarnessConversationStateRow, row.conversation_state_id
            )
            if state is None or state.version != row.expected_version:
                if row.status == TurnClaimStatus.EFFECT_STARTED.value:
                    row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
                else:
                    row.status = TurnClaimStatus.FAILED.value
                    row.finalized_at = timestamp
                row.final_result = {
                    "stop_reason": "state_conflict",
                    "output": "Conversation State changed before the capability effect.",
                }
                await self._db.flush()
                raise ConversationStateConflict(
                    "Turn claim has a stale Conversation State version"
                )
        return row

    async def _fail_row(
        self,
        row: HarnessConversationTurnClaimRow,
        final_result: dict[str, Any],
        timestamp: datetime.datetime,
    ) -> None:
        row.status = TurnClaimStatus.FAILED.value
        row.final_result = _bounded_result(final_result)
        row.finalized_at = timestamp
        await self._db.flush()

    async def _state_row(
        self, conversation_id: int, owner_user_id: int
    ) -> HarnessConversationStateRow | None:
        return (
            await self._db.execute(
                select(HarnessConversationStateRow).where(
                    HarnessConversationStateRow.conversation_id == conversation_id,
                    HarnessConversationStateRow.owner_user_id == owner_user_id,
                )
            )
        ).scalar_one_or_none()

    async def _require_conversation_owner(
        self, conversation_id: int, owner_user_id: int
    ) -> None:
        conversation = await self._db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != owner_user_id:
            raise ConversationStateDenied("Conversation State is unavailable")


def _to_state(row: HarnessConversationStateRow) -> ConversationState:
    return ConversationState(
        id=row.id,
        conversation_id=row.conversation_id,
        owner_user_id=row.owner_user_id,
        version=row.version,
        objective=row.objective,
        referenced_entities=row.referenced_entities or [],
        requested_inputs=row.requested_inputs or [],
        active_tasks=row.active_tasks or [],
        prepared_action=row.prepared_action,
        response_fingerprints=row.response_fingerprints or [],
        last_applied_message_id=row.last_applied_message_id,
    )


def _to_claim(
    row: HarnessConversationTurnClaimRow, *, lease_token: str | None = None
) -> ConversationTurnClaim:
    return ConversationTurnClaim(
        id=row.id,
        conversation_state_id=row.conversation_state_id,
        message_id=row.message_id,
        expected_version=row.expected_version,
        idempotency_key=row.idempotency_key,
        status=TurnClaimStatus(row.status),
        lease_token=lease_token,
        lease_expires_at=_as_utc(row.lease_expires_at),
        effects=_completed_effects(row.effect_receipts or []),
        final_result=row.final_result,
    )


def _completed_effects(receipts: list[dict[str, Any]]) -> list[ConversationTaskEffect]:
    effects: list[ConversationTaskEffect] = []
    for item in receipts:
        effect = item.get("effect") if isinstance(item, dict) else None
        if item.get("phase") == "completed" and isinstance(effect, dict):
            effects.append(ConversationTaskEffect.model_validate(effect))
    return effects


def _apply_effects(
    state: ConversationState,
    effects: list[ConversationTaskEffect],
    response_fingerprint: str | None,
    *,
    focus_task_ref_id: str | None = None,
    complete_objective: bool = False,
) -> ConversationState:
    tasks = list(state.active_tasks)
    entities = list(state.referenced_entities)
    requested = list(state.requested_inputs)
    objective = state.objective
    if complete_objective:
        requested = [item for item in requested if item.task_ref_id is not None]
        objective = None
    for effect in effects:
        if effect.remove_task_ref_id is not None:
            tasks = [item for item in tasks if item.task_ref_id != effect.remove_task_ref_id]
            requested = [
                item for item in requested if item.task_ref_id != effect.remove_task_ref_id
            ]
            continue
        task = effect.task_ref
        assert task is not None
        tasks = [item for item in tasks if item.task_ref_id != task.task_ref_id]
        if task.focused:
            tasks = [item.model_copy(update={"focused": False, "focus_state": ConversationTaskFocus.INACTIVE}) for item in tasks]
        tasks.append(task)
        requested = [item for item in requested if item.task_ref_id != task.task_ref_id]
        requested.extend(effect.requested_inputs)
        for entity in effect.referenced_entities:
            entities = [
                item
                for item in entities
                if not (
                    item.entity_type == entity.entity_type
                    and item.stable_id == entity.stable_id
                )
            ]
            entities.append(entity)
        objective = effect.objective or objective
        if effect.objective is not None:
            requested = [item for item in requested if item.task_ref_id is not None]
    tasks = _normalize_focus(tasks)
    if focus_task_ref_id is not None and any(
        item.task_ref_id == focus_task_ref_id for item in tasks
    ):
        tasks = [
            item.model_copy(update={"focused": item.task_ref_id == focus_task_ref_id, "focus_state": ConversationTaskFocus.FOCUSED if item.task_ref_id == focus_task_ref_id else ConversationTaskFocus.INACTIVE})
            for item in tasks
        ]
    valid_ids = {item.task_ref_id for item in tasks}
    requested = [
        item
        for item in requested
        if item.task_ref_id is None or item.task_ref_id in valid_ids
    ]
    if not tasks and not requested:
        objective = None
    fingerprints = list(state.response_fingerprints)
    if response_fingerprint and response_fingerprint not in fingerprints:
        fingerprints.append(response_fingerprint)
    return state.model_copy(
        update={
            "objective": objective,
            "active_tasks": tasks,
            "requested_inputs": requested[:30],
            "referenced_entities": entities[-30:],
            "response_fingerprints": fingerprints[-_MAX_FINGERPRINTS:],
        }
    )


def _normalize_focus(
    tasks: list[ConversationTaskReference],
) -> list[ConversationTaskReference]:
    focused_seen = False
    normalized: list[ConversationTaskReference] = []
    for item in reversed(tasks):
        focused = item.focus_state is ConversationTaskFocus.FOCUSED and not focused_seen
        focused_seen = focused_seen or focused
        normalized.append(item.model_copy(update={"focused": focused, "focus_state": ConversationTaskFocus.FOCUSED if focused else ConversationTaskFocus.INACTIVE if item.focus_state is ConversationTaskFocus.FOCUSED else item.focus_state}))
    result = list(reversed(normalized))
    if result and not any(item.focused for item in result) and not any(item.focus_state in {ConversationTaskFocus.BACKGROUND, ConversationTaskFocus.PAUSED} for item in result):
        result[-1] = result[-1].model_copy(update={"focused": True, "focus_state": ConversationTaskFocus.FOCUSED})
    return result


def _pack_requested_inputs(
    task: ConversationTaskReference, validation: Any
) -> list[ConversationRequestedInput]:
    """Derive requested detail from PackDraftService's validated schema state."""

    if not isinstance(validation, dict) or validation.get("valid") is True:
        return []
    errors = validation.get("errors")
    safe_errors = [
        " ".join(item.split())[:160]
        for item in errors or []
        if isinstance(item, str) and item.strip()
    ][:5]
    detail = "; ".join(safe_errors)
    prompt = (
        "Provide the missing Pack purpose, analyses, evidence, outputs, and audience details."
        + (f" Current validation: {detail}" if detail else "")
    )[:500]
    return [
        ConversationRequestedInput(
            task_ref_id=task.task_ref_id,
            field="pack_details",
            prompt=prompt,
        )
    ]


def _bounded_mapping(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "kind",
        "draft_id",
        "expected_revision",
        "target_revision",
        "revision",
        "workflow_run_id",
        "project_id",
        "status",
        "private_pack_version_id",
        "pack_id",
        "version",
        "name",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
    return result


def _bounded_result(value: dict[str, Any]) -> dict[str, Any]:
    # Model validation/transport code already sanitizes results.  This final
    # bound prevents claim rows from becoming an unbounded transcript store.
    import json

    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    if len(raw) > _MAX_FINAL_RESULT_CHARS:
        return {"stop_reason": value.get("stop_reason"), "truncated": True}
    return value


__all__ = [
    "ConversationEntityReference",
    "ConversationEffectReceipt",
    "ConversationRequestedInput",
    "ConversationState",
    "ConversationStateConflict",
    "ConversationStateDenied",
    "ConversationStateRepository",
    "ConversationTaskEffect",
    "ConversationTaskKind",
    "ConversationTaskReference",
    "ConversationTurnClaim",
    "StateTransitionSource",
    "TurnClaimStatus",
]
