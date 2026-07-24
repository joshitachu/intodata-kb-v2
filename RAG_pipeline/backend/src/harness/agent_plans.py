"""Single mutation authority for Slice 4's non-executable AgentPlan state."""
# ruff: noqa: E701, E702
from __future__ import annotations

import datetime
import secrets
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.harness import HarnessAgentPlanEventRow, HarnessAgentPlanRow, HarnessAgentPlanStepRow, HarnessWorkflowRunRow
from src.harness.conversation_state import ConversationStateDenied, ConversationTurnClaim
from src.harness.models import HarnessRiskTier
from src.harness.registry import CapabilityRegistry
from src.harness import UnknownCapabilityError
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest, CapabilityHandlerServices, CapabilityObservation
from src.harness.models import CapabilityDefinition, CapabilityHealth, HarnessProviderType

_MAX_TEXT = 2_000
_MAX_LIST = 20
_STEP_KINDS = frozenset({"read", "effect", "workflow", "approval", "wait"})


class AgentPlanError(ValueError): pass
class AgentPlanConflict(AgentPlanError): pass
class AgentPlanNotFound(AgentPlanError): pass


class AgentPlanStatus(StrEnum):
    ACTIVE = "active"; PAUSED = "paused"; ABANDONED = "abandoned"


class AgentPlanStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]{0,79}$")
    objective: str = Field(min_length=1, max_length=_MAX_TEXT)
    success_criteria: list[str] = Field(default_factory=list, max_length=_MAX_LIST)
    capability_id: str | None = Field(default=None, max_length=160)
    kind: str = Field(pattern="^(read|effect|workflow|approval|wait)$")
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    requires_wait: bool = False
    restart_sensitive: bool = False


class AgentPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=_MAX_TEXT)
    success_criteria: list[str] = Field(default_factory=list, max_length=_MAX_LIST)
    constraints: list[str] = Field(default_factory=list, max_length=_MAX_LIST)
    scoped_entities: list[str] = Field(default_factory=list, max_length=_MAX_LIST)
    steps: list[AgentPlanStepInput] = Field(min_length=2, max_length=12)


class AgentPlanStepProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str; objective: str; success_criteria: list[str]; capability_id: str | None
    kind: str; depends_on: list[str]; requires_wait: bool; restart_sensitive: bool
    status: str = "pending"
    effect_operation_id: str | None = None
    capability_receipt_ref: dict | None = None
    workflow_run_id: int | None = None
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class AgentPlanProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str; goal: str; success_criteria: list[str]; constraints: list[str]
    scoped_entities: list[str]; status: AgentPlanStatus; version: int
    steps: list[AgentPlanStepProjection]; last_change_reason: str | None = None
    created_at: datetime.datetime; updated_at: datetime.datetime

    def model_projection(self) -> dict:
        return self.model_dump(mode="json", exclude={"created_at", "updated_at"})


def topology_requires_plan(steps: Iterable[AgentPlanStepInput]) -> bool:
    """Decide solely from validated topology, never user wording."""
    values = list(steps)
    non_read_steps = [item for item in values if item.kind != "read"]
    return (
        len(non_read_steps) > 1
        or any(
            item.kind in {"workflow", "approval", "wait"}
            or item.requires_wait
            or item.restart_sensitive
            for item in values
        )
        or any(item.depends_on for item in non_read_steps)
    )


class AgentPlanService:
    def __init__(self, db: AsyncSession, registry: CapabilityRegistry) -> None:
        self._db, self._registry = db, registry

    async def current(self, *, conversation_id: int, owner_user_id: int) -> AgentPlanProjection | None:
        row = await self._owned_row(conversation_id, owner_user_id, include_abandoned=False)
        return await self._projection(row) if row else None

    async def read(self, *, conversation_id: int, owner_user_id: int, plan_id: str | None = None) -> AgentPlanProjection:
        row = await self._owned_row(conversation_id, owner_user_id, plan_id=plan_id, include_abandoned=True)
        if row is None: raise AgentPlanNotFound("No plan exists for this conversation")
        return await self._projection(row)

    async def create(self, *, conversation_id: int, owner_user_id: int, message_id: int, claim: ConversationTurnClaim, payload: AgentPlanInput, project_scope: list[str] | None) -> AgentPlanProjection:
        self._validate_claim(claim, message_id); self._validate_payload(payload, project_scope)
        if not topology_requires_plan(payload.steps): raise AgentPlanError("Validated topology does not require a durable plan")
        if await self._owned_row(conversation_id, owner_user_id, include_abandoned=False): raise AgentPlanConflict("An active plan already exists")
        now = datetime.datetime.now(datetime.UTC)
        row = HarnessAgentPlanRow(plan_id=f"ap-{secrets.token_urlsafe(12)}", conversation_id=conversation_id, owner_user_id=owner_user_id, goal=payload.goal, success_criteria=payload.success_criteria, constraints=payload.constraints, scoped_entities=payload.scoped_entities, status="active", version=1, created_by_message_id=message_id, last_modified_message_id=message_id, created_at=now, updated_at=now)
        self._replace_steps(row, payload.steps); self._db.add(row); await self._db.flush()
        self._event(row, "created", None, "created_from_validated_objective", message_id); await self._db.flush()
        return await self._projection(row)

    async def revise(self, *, conversation_id: int, owner_user_id: int, message_id: int, claim: ConversationTurnClaim, plan_id: str, expected_version: int, payload: AgentPlanInput, project_scope: list[str] | None) -> AgentPlanProjection:
        self._validate_claim(claim, message_id); self._validate_payload(payload, project_scope)
        if not topology_requires_plan(payload.steps): raise AgentPlanError("Validated topology does not require a durable plan")
        row = await self._require_mutable(conversation_id, owner_user_id, plan_id, expected_version)
        old_scope = set(row.scoped_entities or [])
        if not set(payload.scoped_entities).issubset(old_scope): raise AgentPlanError("Revision cannot expand plan scope")
        locked = {step.step_key: step for step in row.steps if step.status != "pending"}
        proposed = {step.key: step for step in payload.steps}
        if any(key not in proposed for key in locked): raise AgentPlanError("Revision cannot remove an executing or completed step")
        for key, prior in locked.items():
            candidate = proposed[key]
            if candidate.objective != prior.objective or candidate.capability_id != prior.capability_id or candidate.depends_on != list(prior.depends_on or []): raise AgentPlanError("Revision cannot rewrite executing or completed step history")
        row.goal, row.success_criteria, row.constraints, row.scoped_entities = payload.goal, payload.success_criteria, payload.constraints, payload.scoped_entities
        preserved = {step.step_key: step for step in row.steps}
        replacement = []
        for index, step in enumerate(payload.steps, 1):
            prior = preserved.get(step.key)
            replacement.append(HarnessAgentPlanStepRow(step_key=step.key, ordinal=index, objective=step.objective, success_criteria=step.success_criteria, capability_id=step.capability_id, topology_kind=step.kind, depends_on=step.depends_on, requires_wait=step.requires_wait, restart_sensitive=step.restart_sensitive, status=prior.status if prior else "pending", effect_operation_id=prior.effect_operation_id if prior else None, capability_receipt_ref=prior.capability_receipt_ref if prior else None, workflow_run_id=prior.workflow_run_id if prior else None, started_at=prior.started_at if prior else None, finished_at=prior.finished_at if prior else None))
        row.steps[:] = replacement; await self._advance(row, expected_version, message_id, "revised", "revised_from_user_turn")
        return await self._projection(row)

    async def record_capability_outcome(self, *, conversation_id: int, owner_user_id: int, message_id: int, capability_id: str, operation_id: str, status: str, receipt_ref: dict | None = None, workflow_ref: dict | None = None) -> AgentPlanProjection | None:
        """Advance one dependency-ready step from authoritative effect evidence."""
        row = await self._owned_row(conversation_id, owner_user_id, include_abandoned=False)
        if row is None or row.status != "active": return None
        completed = {step.step_key for step in row.steps if step.status == "completed"}
        candidates = [step for step in sorted(row.steps, key=lambda item: item.ordinal) if step.capability_id == capability_id and step.status in {"pending", "executing", "awaiting_approval", "waiting"} and set(step.depends_on or []).issubset(completed)]
        if not candidates: return await self._projection(row)
        step = candidates[0]; now = datetime.datetime.now(datetime.UTC)
        if step.effect_operation_id not in {None, operation_id}:
            if not (step.status == "awaiting_approval" and str(step.effect_operation_id).startswith("approval:")):
                raise AgentPlanConflict("Plan step is linked to a different effect operation")
        step.effect_operation_id = operation_id
        if step.started_at is None: step.started_at = now
        step.capability_receipt_ref = receipt_ref
        workflow_id = (workflow_ref or {}).get("workflow_run_id", (workflow_ref or {}).get("id"))
        step.workflow_run_id = workflow_id if isinstance(workflow_id, int) else step.workflow_run_id
        if status == "approval_required": step.status = "awaiting_approval"
        elif status in {"triggered"} or (status == "succeeded" and workflow_ref): step.status = "waiting"
        elif status == "succeeded": step.status = "completed"; step.finished_at = now
        elif status in {"failed", "cancelled"}: step.status = "failed"; step.finished_at = now
        else: return await self._projection(row)
        await self._advance(row, row.version, message_id, "step_outcome", f"step_{step.status}_from_authoritative_outcome")
        return await self._projection(row)

    async def reconcile_workflow_outcomes(self, *, conversation_id: int, owner_user_id: int, message_id: int) -> AgentPlanProjection | None:
        """Refresh waiting steps only from owner-scoped terminal ledger truth."""
        row = await self._owned_row(conversation_id, owner_user_id, include_abandoned=False)
        if row is None: return None
        waiting = [step for step in row.steps if step.status == "waiting" and step.workflow_run_id is not None]
        if not waiting: return await self._projection(row)
        changed = False; now = datetime.datetime.now(datetime.UTC)
        for step in waiting:
            run = (await self._db.execute(select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == step.workflow_run_id, HarnessWorkflowRunRow.conversation_id == conversation_id, HarnessWorkflowRunRow.user_id == owner_user_id))).scalar_one_or_none()
            if run is None: continue
            if run.status == "succeeded": step.status = "completed"; step.finished_at = run.finished_at or now; changed = True
            elif run.status in {"failed", "cancelled"}: step.status = run.status; step.finished_at = run.finished_at or run.cancelled_at or now; changed = True
        if changed: await self._advance(row, row.version, message_id, "workflow_reconciled", "step_terminal_from_workflow_ledger")
        return await self._projection(row)

    async def transition(self, *, conversation_id: int, owner_user_id: int, message_id: int, claim: ConversationTurnClaim, plan_id: str, expected_version: int, action: str) -> AgentPlanProjection:
        self._validate_claim(claim, message_id); row = await self._require_mutable(conversation_id, owner_user_id, plan_id, expected_version)
        targets = {"pause": ("active", "paused", "paused_by_user"), "resume": ("paused", "active", "resumed_by_user"), "abandon": (("active", "paused"), "abandoned", "abandoned_by_user")}
        if action not in targets: raise AgentPlanError("Unsupported plan action")
        source, target, reason = targets[action]
        if row.status not in (source if isinstance(source, tuple) else (source,)): raise AgentPlanError("Illegal plan transition")
        row.status = target; await self._advance(row, expected_version, message_id, action, reason)
        return await self._projection(row)

    def _validate_claim(self, claim: ConversationTurnClaim, message_id: int) -> None:
        if claim.message_id != message_id or not claim.lease_token or claim.replayed: raise ConversationStateDenied("A live conversation turn claim is required")

    def _validate_payload(self, payload: AgentPlanInput, project_scope: list[str] | None) -> None:
        keys = [step.key for step in payload.steps]
        if len(keys) != len(set(keys)): raise AgentPlanError("Plan step keys must be unique")
        if project_scope is not None and not set(payload.scoped_entities).issubset(set(project_scope)): raise AgentPlanError("Plan scope is outside authenticated project scope")
        for step in payload.steps:
            if step.key in step.depends_on or not set(step.depends_on).issubset(set(keys)): raise AgentPlanError("Plan dependencies are invalid")
            if step.capability_id:
                try:
                    capability = self._registry.get(step.capability_id)
                except UnknownCapabilityError as exc:
                    raise AgentPlanError("Unknown proposed capability") from exc
                if capability.risk_tier == HarnessRiskTier.READ_ONLY and step.kind != "read": raise AgentPlanError("Capability topology kind is invalid")
        graph = {step.key: step.depends_on for step in payload.steps}
        visiting, visited = set(), set()
        def visit(key: str) -> None:
            if key in visiting: raise AgentPlanError("Plan dependencies must be acyclic")
            if key not in visited:
                visiting.add(key); [visit(dep) for dep in graph[key]]; visiting.remove(key); visited.add(key)
        [visit(key) for key in graph]

    async def _owned_row(self, conversation_id: int, owner_user_id: int, plan_id: str | None = None, include_abandoned: bool = True) -> HarnessAgentPlanRow | None:
        query = select(HarnessAgentPlanRow).where(HarnessAgentPlanRow.conversation_id == conversation_id, HarnessAgentPlanRow.owner_user_id == owner_user_id).options(selectinload(HarnessAgentPlanRow.steps))
        if plan_id: query = query.where(HarnessAgentPlanRow.plan_id == plan_id)
        if not include_abandoned: query = query.where(HarnessAgentPlanRow.status != "abandoned")
        return (await self._db.execute(query.order_by(HarnessAgentPlanRow.id.desc()))).scalars().first()

    async def _require_mutable(self, conversation_id: int, owner_user_id: int, plan_id: str, expected_version: int) -> HarnessAgentPlanRow:
        row = await self._owned_row(conversation_id, owner_user_id, plan_id, include_abandoned=True)
        if row is None: raise AgentPlanNotFound("Plan not found")
        if row.status == "abandoned" or row.version != expected_version: raise AgentPlanConflict("Plan version is stale; reload before retrying")
        return row

    def _replace_steps(self, row: HarnessAgentPlanRow, steps: list[AgentPlanStepInput]) -> None:
        row.steps[:] = [HarnessAgentPlanStepRow(step_key=step.key, ordinal=index, objective=step.objective, success_criteria=step.success_criteria, capability_id=step.capability_id, topology_kind=step.kind, depends_on=step.depends_on, requires_wait=step.requires_wait, restart_sensitive=step.restart_sensitive, status="pending") for index, step in enumerate(steps, 1)]

    async def _advance(self, row: HarnessAgentPlanRow, expected: int, message_id: int, action: str, reason: str) -> None:
        now = datetime.datetime.now(datetime.UTC)
        result = await self._db.execute(update(HarnessAgentPlanRow).where(HarnessAgentPlanRow.id == row.id, HarnessAgentPlanRow.version == expected).values(version=expected + 1, last_modified_message_id=message_id, updated_at=now))
        if result.rowcount != 1: raise AgentPlanConflict("Plan version is stale; reload before retrying")
        row.version = expected + 1; row.last_modified_message_id = message_id; row.updated_at = now; self._event(row, action, expected, reason, message_id); await self._db.flush()

    def _event(self, row: HarnessAgentPlanRow, action: str, previous: int | None, reason: str, message_id: int) -> None:
        self._db.add(HarnessAgentPlanEventRow(plan_id=row.id, action=action, from_version=previous, to_version=row.version, reason_code=reason, change_summary={"status": row.status, "step_count": len(row.steps)}, message_id=message_id, occurred_at=datetime.datetime.now(datetime.UTC)))

    async def _projection(self, row: HarnessAgentPlanRow) -> AgentPlanProjection:
        latest = (await self._db.execute(select(HarnessAgentPlanEventRow.reason_code).where(HarnessAgentPlanEventRow.plan_id == row.id).order_by(HarnessAgentPlanEventRow.id.desc()).limit(1))).scalar_one_or_none()
        return AgentPlanProjection(plan_id=row.plan_id, goal=row.goal, success_criteria=list(row.success_criteria or []), constraints=list(row.constraints or []), scoped_entities=list(row.scoped_entities or []), status=AgentPlanStatus(row.status), version=row.version, steps=[AgentPlanStepProjection(key=s.step_key, objective=s.objective, success_criteria=list(s.success_criteria or []), capability_id=s.capability_id, kind=s.topology_kind, depends_on=list(s.depends_on or []), requires_wait=s.requires_wait, restart_sensitive=s.restart_sensitive, status=s.status, effect_operation_id=s.effect_operation_id, capability_receipt_ref=s.capability_receipt_ref, workflow_run_id=s.workflow_run_id, started_at=s.started_at, finished_at=s.finished_at) for s in sorted(row.steps, key=lambda value: value.ordinal)], last_change_reason=latest, created_at=row.created_at, updated_at=row.updated_at)


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_STEP_SCHEMA = _schema({"key": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,79}$"}, "objective": {"type": "string", "maxLength": 2000}, "success_criteria": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "capability_id": {"type": "string"}, "kind": {"type": "string", "enum": ["read", "effect", "workflow", "approval", "wait"]}, "depends_on": {"type": "array", "items": {"type": "string"}, "maxItems": 8}, "requires_wait": {"type": "boolean"}, "restart_sensitive": {"type": "boolean"}}, ["key", "objective", "kind"])
_PLAN_PROPERTIES = {"goal": {"type": "string", "maxLength": 2000}, "success_criteria": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "constraints": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "scoped_entities": {"type": "array", "items": {"type": "string"}, "maxItems": 20}, "steps": {"type": "array", "items": _STEP_SCHEMA, "minItems": 2, "maxItems": 12}}


def agent_plan_capabilities() -> tuple[CapabilityDefinition, ...]:
    """Exact Slice 4 plan-only capability catalogue."""
    values = []
    schemas = {
        "project_objective": _schema({"summary": {"type": "string", "maxLength": 2000}, "proposed_steps": {"type": "array", "items": _STEP_SCHEMA, "minItems": 1, "maxItems": 12}}, ["summary", "proposed_steps"]),
        "create_agent_plan": _schema(_PLAN_PROPERTIES, ["goal", "steps"]),
        "read_agent_plan": _schema({"plan_id": {"type": "string"}}, []),
        "revise_agent_plan": _schema({"plan_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 1}, **_PLAN_PROPERTIES}, ["plan_id", "expected_version", "goal", "steps"]),
        "pause_agent_plan": _schema({"plan_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 1}}, ["plan_id", "expected_version"]),
        "resume_agent_plan": _schema({"plan_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 1}}, ["plan_id", "expected_version"]),
        "abandon_agent_plan": _schema({"plan_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 1}}, ["plan_id", "expected_version"]),
    }
    for action, input_schema in schemas.items():
        values.append(CapabilityDefinition(id=f"nexus.agent_plan.{action}", label=action.replace("_", " ").title(), provider_type=HarnessProviderType.WORKFLOW, provider_id="nexus.agent_plan", action=action, input_schema=input_schema, output_type="agent_plan_projection", risk_tier=HarnessRiskTier.READ_ONLY if action == "project_objective" else HarnessRiskTier.REVERSIBLE_WRITE, enabled=True, health=CapabilityHealth.AVAILABLE, semantic_name=action, purpose="Project or govern a non-executable durable AgentPlan.", use_when=["Plan state is needed for the user objective."], do_not_use_when=["A domain action should be executed."], preconditions=["Live conversation turn claim for mutations."], output_contracts=["bounded user-safe AgentPlan projection"], failure_recovery={"validation_failed": "Correct the bounded plan arguments.", "denied": "Explain the governed boundary.", "failed": "Reload plan state and retry once."}, metadata={"execution_plane": "agent_plan_control", "requires_turn_claim": action != "project_objective", "evidence_state_relevant": False}))
    return tuple(values)


class AgentPlanCapabilityHandler:
    def __init__(self, capability: CapabilityDefinition) -> None:
        self.capability_id, self.input_schema, self.output_type = capability.id, capability.input_schema, capability.output_type
        self.task_types = ()

    async def __call__(self, capability: CapabilityDefinition, request: CapabilityExecutionRequest, context: CapabilityExecutionContext, services: CapabilityHandlerServices) -> CapabilityObservation:
        action = capability.action
        raw_steps = request.arguments.get("proposed_steps") if action == "project_objective" else request.arguments.get("steps")
        if action == "project_objective":
            steps = [AgentPlanStepInput.model_validate(item) for item in raw_steps or []]
            return CapabilityObservation(capability_id=capability.id, status="succeeded", output_type=capability.output_type, summary="Objective topology projected without persistence.", data={"summary": request.arguments["summary"], "plan_required": topology_requires_plan(steps), "step_count": len(steps)})
        service = services.agent_plan_service
        state_repository = services.conversation_state_repository
        if service is None or state_repository is None or context.conversation_turn_claim_id is None or context.conversation_turn_claim_token is None or context.current_message_id is None:
            raise ConversationStateDenied("Plan control is not configured for this turn")
        claim = await state_repository.validate_claim(claim_id=context.conversation_turn_claim_id, lease_token=context.conversation_turn_claim_token)
        if action == "create_agent_plan": projection = await service.create(conversation_id=context.conversation_id, owner_user_id=context.user_id, message_id=context.current_message_id, claim=claim, payload=AgentPlanInput.model_validate(request.arguments), project_scope=context.project_scope)
        elif action == "read_agent_plan": projection = await service.read(conversation_id=context.conversation_id, owner_user_id=context.user_id, plan_id=request.arguments.get("plan_id"))
        elif action == "revise_agent_plan":
            payload = AgentPlanInput.model_validate({key: value for key, value in request.arguments.items() if key not in {"plan_id", "expected_version"}})
            projection = await service.revise(conversation_id=context.conversation_id, owner_user_id=context.user_id, message_id=context.current_message_id, claim=claim, plan_id=request.arguments["plan_id"], expected_version=request.arguments["expected_version"], payload=payload, project_scope=context.project_scope)
        else: projection = await service.transition(conversation_id=context.conversation_id, owner_user_id=context.user_id, message_id=context.current_message_id, claim=claim, plan_id=request.arguments["plan_id"], expected_version=request.arguments["expected_version"], action=action.removesuffix("_agent_plan"))
        data = projection.model_dump(mode="json")
        return CapabilityObservation(capability_id=capability.id, status="succeeded", output_type=capability.output_type, summary=f"Plan is {projection.status} at version {projection.version}.", data=data, evidence_refs=[{"evidence_id": f"agent-plan:{projection.plan_id}:v{projection.version}", "source_type": "agent_plan"}])
