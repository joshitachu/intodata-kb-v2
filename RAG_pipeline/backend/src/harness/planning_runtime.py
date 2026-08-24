"""Slice 4 standard-runtime assembly for the plan-control cohort.

Only reads and the seven AgentPlan controls are model-visible. Domain writes are
filtered before the model sees a tool and the gateway independently allowlists
the plan execution plane.
"""
from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import (
    INTERACTION_PROTOCOL_PROMPT,
    build_turn_assessment_tool,
)
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import ReadOnlyPrimaryRuntime
from src.harness.registry import CapabilityRegistry
from src.harness.semantic_tools import GatewayMode, GovernanceClass, SemanticEventLedger, SemanticToolGateway, SemanticToolMiddleware, build_semantic_structured_tools, build_semantic_tool_snapshot
from src.harness.run_budgets import LayeredBudgetController, LayeredModelBudgetMiddleware
from src.harness.turn_instrumentation import ModelLatencyMiddleware

PLAN_CONTROL_PREFIX = "nexus.agent_plan."
PLAN_PRIMARY_SYSTEM_PROMPT = INTERACTION_PROTOCOL_PROMPT + """

You are Nexus handling a planning-cohort conversation turn.

When the turn assessment identifies an action-ready planning request, call
`project_objective` exactly once before any durable plan mutation. Greetings,
explanations, discussion, preparation, and unrelated conversation must answer
normally without projecting an objective. Projection stores no private
reasoning and does not execute a domain action.

After the projection: answer normally when `plan_required` is false. When it is
true, create or revise one durable AgentPlan before describing the proposed plan.
Use plan-control tools only for plan state. Never claim a plan step completed,
failed, approved, or executed. Never attempt Packs, documentation, onboarding,
workflow, approval, connector, or any other domain action.

Read tools may be used only when evidence is needed. Ground material factual
claims in tool evidence and honestly disclose missing, partial, denied, or failed
evidence. A current plan is background context; do not force unrelated answers
to discuss it. Never reveal credentials, internal capability identifiers, raw
exceptions, private reasoning, or raw tool payloads.

End each reply with a fenced `nexus-grounding` JSON block containing answer
status, citations, and disclosures. The prose above it is the visible answer.
"""


def build_planning_primary_runtime(*, model: BaseChatModel, registry: CapabilityRegistry, executor: HarnessCapabilityExecutor, context: CapabilityExecutionContext, plan_context: dict | None = None, turn_context: str = "", interrupt_before_tools: bool = False, budget_controller: LayeredBudgetController | None = None) -> ReadOnlyPrimaryRuntime:
    allowed = {item.id for item in registry.list(enabled_only=True) if item.risk_tier.value == "read_only" or item.id.startswith(PLAN_CONTROL_PREFIX)}
    snapshot = build_semantic_tool_snapshot(registry, scope=ManifestScope(permissions=context.permissions, project_ids=context.project_scope, include_bounded_loop_only=True, allowed_capability_ids=allowed, denied_capability_ids=context.denied_capability_ids))
    model_tools = tuple(item for item in snapshot.tools if item.governance_class == GovernanceClass.READ or item.canonical_id.startswith(PLAN_CONTROL_PREFIX))
    ledger = SemanticEventLedger()
    gateway = SemanticToolGateway(snapshot=snapshot, executor=executor, context=context, ledger=ledger, mode=GatewayMode.PLAN_CONTROL_PRIMARY, registry=registry, budget_controller=budget_controller)
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        required_projection=True,
        required_plan_action=(
            "revise_agent_plan" if plan_context is not None else "create_agent_plan"
        ),
        require_interaction_assessment=True,
        mode=GatewayMode.PLAN_CONTROL_PRIMARY,
        budget_controller=budget_controller,
    )
    tools = [
        build_turn_assessment_tool(),
        *build_semantic_structured_tools(
            snapshot.model_copy(update={"tools": model_tools}), gateway
        ),
    ]
    state_text = "No active durable plan." if plan_context is None else f"Current background plan: {plan_context}"
    middlewares = [ModelLatencyMiddleware(), middleware] + ([LayeredModelBudgetMiddleware(budget_controller, model_name=str(getattr(model, "model_name", getattr(model, "model", ""))))] if budget_controller else [])
    graph = create_agent(model, tools, system_prompt=f"{PLAN_PRIMARY_SYSTEM_PROMPT}\nCurrent background plan: {state_text}\nAuthoritative turn context: {turn_context}", middleware=middlewares, interrupt_before=["tools"] if interrupt_before_tools else None, name="nexus_slice_4_plan_primary")
    return ReadOnlyPrimaryRuntime(graph=graph, snapshot=snapshot, gateway=gateway, middleware=middleware, ledger=ledger, read_only_tools=tuple(item for item in model_tools if item.governance_class == GovernanceClass.READ), filtered_consequential=tuple(item.canonical_id for item in snapshot.tools if item not in model_tools))
