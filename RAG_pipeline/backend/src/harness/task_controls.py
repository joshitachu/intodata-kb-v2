"""Governed conversation-focus controls distinct from domain lifecycle."""

from __future__ import annotations

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityHandlerServices,
    CapabilityObservation,
)
from src.harness.conversation_state import (
    ConversationStateDenied,
    ConversationTaskEffect,
    ConversationTaskFocus,
)
from src.harness.models import CapabilityDefinition, CapabilityHealth, HarnessProviderType, HarnessRiskTier


def _schema() -> dict:
    return {
        "type": "object",
        "required": ["task_ref_id"],
        "properties": {"task_ref_id": {"type": "string", "minLength": 1, "maxLength": 255}},
        "additionalProperties": False,
    }


def conversation_task_control_capabilities() -> tuple[CapabilityDefinition, ...]:
    values = []
    descriptions = {
        "background_conversation_task": (
            "Move a conversational task out of focus without pausing, cancelling, deleting, or abandoning its domain work.",
            ConversationTaskFocus.BACKGROUND,
        ),
        "pause_conversation_task": (
            "Pause conversational prompting for a task without cancelling or changing its domain workflow or draft.",
            ConversationTaskFocus.PAUSED,
        ),
        "resume_conversation_task": (
            "Return an exact background or paused task to conversational focus without starting a duplicate domain task.",
            ConversationTaskFocus.FOCUSED,
        ),
    }
    for action, (purpose, _) in descriptions.items():
        values.append(
            CapabilityDefinition(
                id=f"nexus.conversation_tasks.{action}",
                label=action.replace("_", " ").title(),
                semantic_name=action,
                purpose=purpose,
                preconditions=["The exact owner-scoped task reference is present in Conversation State."],
                failure_recovery={
                    "validation_failed": "Use an exact visible task reference.",
                    "denied": "Do not alter domain lifecycle; explain the focus boundary.",
                    "failed": "Reload task state and retry once.",
                },
                provider_type=HarnessProviderType.WORKFLOW,
                provider_id="nexus.conversation_state",
                action=action,
                input_schema=_schema(),
                output_type="conversation_task_focus",
                risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
                enabled=True,
                health=CapabilityHealth.AVAILABLE,
                packs=["core"],
                task_types=["conversation_focus"],
                use_when=[purpose],
                do_not_use_when=["The user asked to cancel a workflow or abandon/delete a draft or plan."],
                output_contracts=["owner-scoped task focus projection"],
                metadata={"bounded_loop_only": True, "requires_turn_claim": True, "agent_control_plane": True},
            )
        )
    return tuple(values)


class ConversationTaskControlHandler:
    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation:
        state_repo = services.conversation_state_repository
        if (
            state_repo is None
            or context.conversation_id is None
            or context.current_message_id is None
            or context.conversation_turn_claim_id is None
            or context.conversation_turn_claim_token is None
            or request.idempotency_key is None
        ):
            raise ConversationStateDenied("Conversation task focus is not configured")
        claim_id = context.conversation_turn_claim_id
        token = context.conversation_turn_claim_token
        replay = await state_repo.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=request.idempotency_key,
            capability_id=capability.id,
            authority_hint={"task_ref_id": request.arguments["task_ref_id"], "kind": "conversation_focus"},
        )
        if replay is not None:
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                output_type=capability.output_type,
                summary="Conversation task focus was already updated.",
                task_ref=replay.effect.task_ref,
                receipt_ref={"idempotency_key": request.idempotency_key},
            )
        state = await state_repo.load(conversation_id=context.conversation_id, owner_user_id=context.user_id)
        task_ref_id = request.arguments["task_ref_id"]
        task = next((item for item in state.active_tasks if item.task_ref_id == task_ref_id), None)
        if task is None:
            await state_repo.abort_effect(claim_id=claim_id, lease_token=token, call_key=request.idempotency_key, capability_id=capability.id, reason="Task reference is unavailable")
            raise ConversationStateDenied("Conversation task reference is unavailable")
        target = {
            "background_conversation_task": ConversationTaskFocus.BACKGROUND,
            "pause_conversation_task": ConversationTaskFocus.PAUSED,
            "resume_conversation_task": ConversationTaskFocus.FOCUSED,
        }[capability.action]
        updated = task.model_copy(update={"focus_state": target, "focused": target is ConversationTaskFocus.FOCUSED})
        requested = [item for item in state.requested_inputs if item.task_ref_id == task_ref_id]
        await state_repo.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=request.idempotency_key,
                capability_id=capability.id,
                task_ref=updated,
                requested_inputs=requested,
            ),
            domain_ref={"task_ref_id": task_ref_id, "focus_state": target.value},
        )
        return CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary=f"Conversation task is now {target.value}; domain work was unchanged.",
            data={"task_ref_id": task_ref_id, "focus_state": target.value, "domain_state_changed": False},
            task_ref=updated,
            receipt_ref={"idempotency_key": request.idempotency_key},
        )


__all__ = ["ConversationTaskControlHandler", "conversation_task_control_capabilities"]
