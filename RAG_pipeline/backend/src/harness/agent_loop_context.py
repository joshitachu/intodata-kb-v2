"""Authoritative database context seam for the bounded harness agent loop."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.conversation import Conversation
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecisionModel,
    AgentLoopInput,
    AgentLoopLimits,
    AgentLoopResult,
    AgentStopReason,
    BoundedAgentLoop,
    authoritative_state_version,
    response_fingerprint,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.conversation_state import (
    ConversationState,
    ConversationStateConflict,
    ConversationStateRepository,
)
from src.db.models.harness import HarnessConversationTurnClaimRow
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.response_synthesis import (
    ResponseKind,
    ResponseSynthesisPlan,
    SynthesisSource,
    referenced_workflow_ids,
    render_typed_response,
)
from src.harness.registry import CapabilityRegistry
from src.harness.repository import HarnessWorkflowRepository
from src.harness.workflow_projection import AuthoritativeWorkflowCardProjector


_HISTORY_LIMIT = 20
_WORKFLOW_LIMIT = 20
_MAX_STATE_TEXT = 1_000
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bAuthorization\s*[:=]\s*(?:Bearer|Basic)\s+\S+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"
        r"\s*[:=]\s*['\"]?[^\s,'\"}]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


async def run_authoritative_agent_turn(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    permissions: set[str] | None,
    access_token: str | None,
    registry: CapabilityRegistry,
    executor: HarnessCapabilityExecutor,
    model: AgentDecisionModel,
    limits: AgentLoopLimits | None = None,
    approvals: set[str] | None = None,
    allow_writes_content: bool = False,
    allow_external_side_effects: bool = False,
    denied_capability_ids: set[str] | None = None,
    prior_response_fingerprints: set[str] | None = None,
    images: list[str] | None = None,
    message_id: int | None = None,
    state_repository: ConversationStateRepository | None = None,
    workflow_card_projector: AuthoritativeWorkflowCardProjector | None = None,
) -> AgentLoopResult:
    """Load safe conversation/ledger facts and execute the registered loop.

    Workflow activity events are intentionally not read: Slice 1's parent and
    exact child receipts are the state authority.
    """

    state_repo = state_repository or ConversationStateRepository(db)
    state = await state_repo.reconcile_authorities(
        conversation_id=conversation.id,
        owner_user_id=user_id,
    )
    card_projector = workflow_card_projector or AuthoritativeWorkflowCardProjector(
        HarnessWorkflowRepository(db)
    )
    active_workflow_ids = [
        task.workflow_run_id for task in state.active_tasks if task.workflow_run_id is not None
    ]
    if active_workflow_ids:
        await card_projector.project_many(
            active_workflow_ids,
            owner_user_id=user_id,
            conversation_id=conversation.id,
            project_scope=project_scope,
            access_token=access_token,
            reconcile=True,
        )
        state = await state_repo.reconcile_authorities(
            conversation_id=conversation.id,
            owner_user_id=user_id,
        )
    turn = await build_authoritative_agent_input(
        conversation=conversation,
        query=query,
        db=db,
        user_id=user_id,
        project_scope=project_scope,
        prior_response_fingerprints=prior_response_fingerprints,
        state_repository=state_repo,
        conversation_state=state,
        current_message_id=message_id,
        images=images,
    )
    claim = None
    if message_id is not None:
        claim = await state_repo.claim_turn(
            conversation_id=conversation.id,
            owner_user_id=user_id,
            message_id=message_id,
            expected_version=state.version,
            idempotency_key=f"agent-turn:{conversation.id}:{message_id}",
        )
        if claim.replayed:
            if isinstance(claim.final_result, dict):
                try:
                    return AgentLoopResult.model_validate(claim.final_result)
                except ValueError:
                    pass
            return AgentLoopResult(
                output=f"Agent turn failed ({AgentStopReason.STATE_CONFLICT.value}).",
                stop_reason=AgentStopReason.STATE_CONFLICT,
                state_version=turn.state_version or "",
            )
        # The claim must be durable before the model can select a stateful tool.
        await db.commit()

    try:
        result = await BoundedAgentLoop(
            registry=registry,
            executor=executor,
            model=model,
            limits=limits,
        ).run(
            turn,
            context=CapabilityExecutionContext(
                user_id=user_id,
                conversation_id=conversation.id,
                project_scope=project_scope,
                permissions=permissions,
                approvals=approvals or set(),
                access_token=access_token,
                allow_writes_content=allow_writes_content,
                allow_external_side_effects=allow_external_side_effects,
                denied_capability_ids=denied_capability_ids or set(),
                current_message_id=message_id,
                conversation_state_version=(state.version if claim is not None else None),
                conversation_turn_claim_id=(claim.id if claim is not None else None),
                conversation_turn_claim_token=(claim.lease_token if claim is not None else None),
            ),
        )
    except Exception:
        # A handler/model exception may have invalidated the request
        # transaction. Recovery decisions must read the last durable receipt.
        await db.rollback()
        result = AgentLoopResult(
            output=f"Agent turn failed ({AgentStopReason.PLANNER_UNAVAILABLE.value}).",
            stop_reason=AgentStopReason.PLANNER_UNAVAILABLE,
            state_version=turn.state_version or "",
        )
    result = await hydrate_authoritative_workflow_cards(
        result,
        db=db,
        user_id=user_id,
        conversation_id=conversation.id,
        project_scope=project_scope,
        access_token=access_token,
        projector=card_projector,
        reconcile=False,
    )
    result = enforce_canonical_repeat_guard(result, turn.prior_response_fingerprints)
    if claim is None or claim.lease_token is None:
        return result

    try:
        claim_row = await db.get(HarnessConversationTurnClaimRow, claim.id)
        receipts = list(claim_row.effect_receipts or []) if claim_row is not None else []
        has_completed_effect = any(item.get("phase") == "completed" for item in receipts)
        has_started_effect = any(item.get("phase") == "started" for item in receipts)
        completed_adds_task = any(
            isinstance(item.get("effect"), dict)
            and isinstance(item["effect"].get("task_ref"), dict)
            for item in receipts
            if item.get("phase") == "completed"
        )
        honored_objective_completion = (
            result.complete_objective
            and not state.active_tasks
            and any(item.task_ref_id is None for item in state.requested_inputs)
            and not completed_adds_task
        )
        if result.complete_objective != honored_objective_completion:
            result = result.model_copy(update={"complete_objective": honored_objective_completion})
        semantic_dialogue = result.action in {
            AgentBoundaryAction.ANSWER,
            AgentBoundaryAction.CLARIFY,
        }
        focus_task_ref_id = next(
            (
                item.task_ref.task_ref_id
                for item in reversed(result.observations)
                if item.succeeded and item.task_ref is not None
            ),
            None,
        )
        if has_completed_effect or semantic_dialogue:
            await state_repo.finalize_claim(
                claim_id=claim.id,
                lease_token=claim.lease_token,
                action=(
                    result.action.value if result.action is not None else result.stop_reason.value
                ),
                final_result=result.model_dump(mode="json"),
                response_fingerprint=result.response_fingerprint,
                focus_task_ref_id=focus_task_ref_id,
                complete_objective=result.complete_objective,
            )
        else:
            await state_repo.fail_claim(
                claim_id=claim.id,
                lease_token=claim.lease_token,
                final_result=result.model_dump(mode="json"),
                recovery_required=has_started_effect,
            )
        await db.commit()
        return result
    except ConversationStateConflict:
        await db.rollback()
        durable = await db.get(HarnessConversationTurnClaimRow, claim.id)
        if durable is not None and durable.status in {
            "claimed",
            "effect_started",
            "recovery_required",
        }:
            receipts = list(durable.effect_receipts or [])
            indeterminate = any(item.get("phase") in {"started", "completed"} for item in receipts)
            await state_repo.fail_claim(
                claim_id=claim.id,
                lease_token=claim.lease_token,
                final_result={
                    **result.model_dump(mode="json"),
                    "stop_reason": AgentStopReason.STATE_CONFLICT.value,
                },
                recovery_required=indeterminate,
            )
            await db.commit()
        return result.model_copy(
            update={
                "action": None,
                "output": "Conversation State changed while this turn was completing. Reload and retry.",
                "stop_reason": AgentStopReason.STATE_CONFLICT,
                "response_fingerprint": None,
            }
        )


async def hydrate_authoritative_workflow_cards(
    result: AgentLoopResult,
    *,
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
    project_scope: list[str] | None,
    access_token: str | None,
    projector: AuthoritativeWorkflowCardProjector | None = None,
    reconcile: bool = True,
) -> AgentLoopResult:
    """Reload and attach exact Ledger cards before persistence or transport."""

    typed = result.typed_response
    if typed is None:
        return result
    run_ids = referenced_workflow_ids(typed.facts)
    run_ids.extend(
        run_id
        for observation in result.observations
        if isinstance(observation.workflow_ref, dict)
        and isinstance((run_id := observation.workflow_ref.get("workflow_run_id")), int)
        and run_id > 0
    )
    if not run_ids:
        return result
    card_projector = projector or AuthoritativeWorkflowCardProjector(HarnessWorkflowRepository(db))
    cards = await card_projector.project_many(
        run_ids,
        owner_user_id=user_id,
        conversation_id=conversation_id,
        project_scope=project_scope,
        access_token=access_token,
        reconcile=reconcile,
    )
    if not cards:
        return result
    card_ids = {card.workflow_run_id for card in cards}
    unsuperseded_facts = [
        fact
        for fact in typed.facts
        if not (
            fact.workflow_run_id in card_ids
            and fact.fact_id.startswith(f"workflow:{fact.workflow_run_id}:")
        )
    ]
    rendered = render_typed_response(
        action=result.action.value if result.action is not None else None,
        content=None,
        plan=ResponseSynthesisPlan(
            fact_ids=[fact.fact_id for fact in unsuperseded_facts],
        ),
        facts=unsuperseded_facts,
        state_version=result.state_version,
        stop_reason=result.stop_reason.value,
        workflow_cards=cards,
        observations=result.observations,
        no_progress=typed.kind is ResponseKind.NO_PROGRESS,
    )
    source = typed.synthesis_source
    if source is SynthesisSource.DIRECT:
        source = SynthesisSource.DETERMINISTIC_FALLBACK
    rendered = rendered.model_copy(update={"synthesis_source": source})
    return result.model_copy(
        update={
            "output": rendered.content,
            "response_fingerprint": rendered.factual_fingerprint,
            "typed_response": rendered,
        }
    )


def enforce_canonical_repeat_guard(
    result: AgentLoopResult, prior_fingerprints: set[str]
) -> AgentLoopResult:
    """Compare the final card-hydrated fingerprint before claim persistence."""

    typed = result.typed_response
    if (
        typed is None
        or result.response_fingerprint not in prior_fingerprints
        or typed.kind is ResponseKind.NO_PROGRESS
    ):
        return result
    card_ids = {card.workflow_run_id for card in typed.workflow_cards}
    base_facts = [
        fact
        for fact in typed.facts
        if not (
            fact.workflow_run_id in card_ids
            and fact.fact_id.startswith(f"workflow:{fact.workflow_run_id}:canonical_")
        )
    ]
    rendered = render_typed_response(
        action=result.action.value if result.action is not None else None,
        content=None,
        plan=ResponseSynthesisPlan(
            fact_ids=[fact.fact_id for fact in base_facts],
        ),
        facts=base_facts,
        state_version=result.state_version,
        stop_reason=AgentStopReason.REPEATED_RESPONSE.value,
        workflow_cards=typed.workflow_cards,
        observations=result.observations,
        no_progress=True,
    )
    return result.model_copy(
        update={
            "output": rendered.content,
            "stop_reason": AgentStopReason.REPEATED_RESPONSE,
            "response_fingerprint": rendered.factual_fingerprint,
            "typed_response": rendered,
        }
    )


def persist_typed_agent_response(message: Any, result: AgentLoopResult) -> dict[str, Any]:
    """Store the exact transport response in the existing structured JSON field."""

    if result.typed_response is None:
        raise ValueError("Agent result has no typed response to persist")
    payload = result.typed_response.model_dump(mode="json")
    message.content = result.typed_response.content
    message.structured_answer = payload
    return payload


def persist_grounded_agent_response(
    message: Any, *, content: str, structured_answer: dict[str, Any]
) -> dict[str, Any]:
    """Store a validated V2 grounded answer in the existing structured JSON field.

    Additive to the legacy typed-response path: it never runs the fact-ID
    renderer and never touches turn claims. The payload uses the additive
    ``agent_response_v2_grounded`` response type that old clients ignore.
    """

    message.content = strip_replay_note(content)
    message.structured_answer = structured_answer
    return structured_answer


#: Prefixed onto a replayed assistant turn that produced no validated evidence,
#: so its specifics cannot be read back as established fact.
_UNVERIFIED_REPLAY_NOTE = (
    "[UNVERIFIED EARLIER ANSWER — no validated project source supported it. Treat "
    "every name, count, system, relationship and figure below as unconfirmed. Do not "
    "repeat any of them as fact and do not build on them; look the subject up again "
    "before making the claim yourself.]\n"
)


#: Matched instead of the whole note: the note carries an em-dash, and the model
#: reproduces the phrase without reliably reproducing the punctuation.
_REPLAY_NOTE_OPENING = "[UNVERIFIED EARLIER ANSWER"


def strip_replay_note(content: str) -> str:
    """Drop the replay marker if the model copied it into its own answer.

    The note is prompt-internal: it is prefixed onto REPLAYED history so an
    unevidenced turn cannot be read back as fact. Observed failure: once several
    history entries carried it, the model opened its own reply with the marker
    verbatim, so an internal instruction addressed to the model reached the chat
    UI as if it were part of the answer.

    Stripped at the persistence boundary rather than in the prompt, because the
    prompt mechanism is correct and still needed -- only the echo is wrong. This
    removes a leading marker, never one quoted mid-answer, so a turn that
    legitimately discusses the note keeps its text.
    """

    if not isinstance(content, str):
        return content
    text = content.lstrip()
    if not text.startswith(_REPLAY_NOTE_OPENING):
        return content
    closing = text.find("]")
    if closing == -1:
        return content
    return text[closing + 1 :].lstrip()


def _is_zero_evidence(message: Any) -> bool:
    """Whether a stored assistant turn was produced with no validated source.

    Reads the same ``evidence_summary.zero_evidence`` flag the UI renders as the
    "No current-turn evidence" badge, so the prompt and the badge can never
    disagree about which answers were unsupported. Absent or malformed payloads
    read as evidenced: a missing flag is not proof of a fabrication, and marking
    a sound answer as unverified would teach the model to distrust its own
    grounded work.
    """

    structured = getattr(message, "structured_answer", None)
    if not isinstance(structured, dict):
        return False
    summary = structured.get("evidence_summary")
    if not isinstance(summary, dict):
        return False
    return summary.get("zero_evidence") is True


def _replayed_content(message: Any) -> str:
    """One history entry's text, with unevidenced answers marked as such.

    The observed failure this closes: a turn invented two systems that do not
    exist in the project, and three turns later those systems were restated as
    fact, with the project's own totals silently adjusted to accommodate them.
    Nothing distinguished that answer from an evidence-backed one once it was
    replayed as flat text.

    Marked rather than dropped. The thread's pronoun resolution is good and
    depends on continuity ("and where does that data go next?" binding to the
    system named one turn earlier), so removing a turn outright would break
    something that works in order to fix something that does not. A prefix
    keeps the shape of the conversation and removes the authority.
    """

    content = message.content
    if message.role != "assistant" or not _is_zero_evidence(message):
        return content
    return f"{_UNVERIFIED_REPLAY_NOTE}{content}"


async def build_authoritative_agent_input(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    prior_response_fingerprints: set[str] | None = None,
    state_repository: ConversationStateRepository | None = None,
    conversation_state: ConversationState | None = None,
    current_message_id: int | None = None,
    images: list[str] | None = None,
) -> AgentLoopInput:
    runs = await HarnessWorkflowRepository(db).list_runs(
        project_scope,
        conversation_id=conversation.id,
        owner_user_id=user_id,
        limit=_WORKFLOW_LIMIT,
    )
    workflow_state = [_safe_workflow(run) for run in runs]
    messages = [
        {"role": item.role, "content": _replayed_content(item)}
        for item in list(conversation.messages)[-_HISTORY_LIMIT:]
        if item.role in {"user", "assistant"}
        and item.content
        and (current_message_id is None or item.id != current_message_id)
    ]
    if conversation_state is None:
        repository = state_repository or ConversationStateRepository(db)
        conversation_state = await repository.reconcile_authorities(
            conversation_id=conversation.id,
            owner_user_id=user_id,
        )
    conversation_projection = conversation_state.model_projection()
    state_version = authoritative_state_version(conversation_projection, workflow_state)
    response_fingerprints = set(conversation_state.response_fingerprints)
    response_fingerprints.update(prior_response_fingerprints or set())
    latest_assistant = next(
        (
            item.content
            for item in reversed(conversation.messages)
            if item.role == "assistant" and item.content
        ),
        None,
    )
    if latest_assistant:
        for action in (AgentBoundaryAction.ANSWER, AgentBoundaryAction.CLARIFY):
            response_fingerprints.add(
                response_fingerprint(
                    action,
                    latest_assistant,
                    state_version=state_version,
                    observations=[],
                )
            )
    return AgentLoopInput(
        user_message=query,
        images=images,
        conversation_history=messages,
        conversation_state=conversation_projection,
        workflow_state=workflow_state,
        state_version=state_version,
        prior_response_fingerprints=response_fingerprints,
    )


def _safe_workflow(run: Any) -> dict[str, Any]:
    return {
        "workflow_run_id": run.id,
        "version": run.version,
        "project_id": run.project_id,
        "capability_id": run.capability_id,
        "status": run.status.value,
        "output_summary": redact_agent_state_text(run.output_summary),
        "error_message": redact_agent_state_text(run.error_message),
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "children": [
            {
                "receipt_id": child.id,
                "version": child.version,
                "provider_id": child.provider_id,
                "external_run_id": child.external_run_id,
                "project_id": child.project_id,
                "status": child.status,
                "provider_status": child.provider_status,
                "provider_timestamp": (
                    child.provider_timestamp.isoformat() if child.provider_timestamp else None
                ),
                "last_observed_at": child.last_observed_at.isoformat(),
                "error_message": redact_agent_state_text(child.error_message),
                "cancellation_state": child.cancellation_state,
            }
            for child in run.children
        ],
    }


def redact_agent_state_text(value: Any) -> str | None:
    """Bound and redact any persisted provider text before model projection."""

    if value is None:
        return None
    result = str(value)[:_MAX_STATE_TEXT]
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[redacted]", result)
    return result


__all__ = [
    "build_authoritative_agent_input",
    "persist_grounded_agent_response",
    "redact_agent_state_text",
    "run_authoritative_agent_turn",
]
