"""Default-off standard runtime for governed reads, effects, and workflows."""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from src.harness.approvals import ApprovalService
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.effect_operations import EffectOperationService
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import MODEL_FACING_READ_EXCLUSIONS, ReadOnlyPrimaryRuntime
from src.harness.registry import CapabilityRegistry
from src.harness.semantic_tools import (
    GatewayMode,
    SemanticEventLedger,
    SemanticToolGateway,
    SemanticToolMiddleware,
    build_semantic_structured_tools,
    build_semantic_tool_snapshot,
)
from src.harness.run_budgets import LayeredBudgetController, LayeredModelBudgetMiddleware


EFFECT_PRIMARY_SYSTEM_PROMPT = """
You are Nexus operating the governed effect runtime.

Respond normally when the user is conversing, asking a question, greeting you,
criticizing you, discussing or negating an action, or making an unrelated
request. These messages do not require a tool call. If a requested action is not
sufficiently specified, ask one concise, contextual question about the missing
business information as a normal assistant message. Do not repeat or lightly
rephrase the user's request as the question, and do not ask the user what
"additional detail" you should use without naming the information you need.

Choose semantic tools by meaning and call one only when the user has requested
the corresponding action and its required arguments are grounded in the
conversation or authoritative background. Never invent placeholders merely to
make a tool call valid. A conversation pause is not workflow cancellation or
draft abandonment. Execute at most one consequential tool per assistant
response; if more work remains, consume its observation and replan sequentially.
Approval-required tools only create an exact pending proposal and must stop until
the owner decides it. Never claim a proposal or workflow is complete without its
typed receipt or terminal Workflow Ledger observation.

Never invent or narrate a pending approval from the request alone. When the
user explicitly asks for an approval-governed action and supplies its required
arguments, call that semantic tool; only an `approval_required` tool observation
proves a pending proposal exists. The requested target argument is authoritative
for the proposal, so do not inspect current state merely to confirm the same
target unless the tool contract requires a current id or version.

Use reads to resolve exact ids and current versions before writes. Respect
validation, denial, conflict, and retry guidance. Never expose credentials,
private reasoning, raw arguments, policy snapshots, hashes, SQL, stack traces,
or provider payloads. A normal assistant message ends the turn.

Authoritative background may contain one `prepared_action`. It is bounded
preparation context, not permission or proof of execution. Merge new user facts
into that exact semantic tool and argument object. When the user confirms it,
submit the exact semantic tool instead of regenerating a larger payload. Prefer
the smallest valid argument object. If a tool returns `needs_input`, use its safe
validation details to ask a contextual question, then stop; do not retry the same
action in that turn.

Before creating or revising a durable AgentPlan, project the proposed objective
topology. If the validated projection says no durable plan is required, answer
without changing plan state.

End the reply with a fenced `nexus-grounding` JSON block containing
`answer_status`, `citations`, and `disclosures`. Cite only evidence ids returned
by tools; direct conversational guidance may use an empty citations list.
"""


def build_effect_primary_runtime(
    *,
    model: BaseChatModel,
    registry: CapabilityRegistry,
    executor: HarnessCapabilityExecutor,
    context: CapabilityExecutionContext,
    approval_service: ApprovalService,
    effect_operation_service: EffectOperationService,
    agent_plan_service=None,  # noqa: ANN001
    background_context: dict | None = None,
    interrupt_before_tools: bool = False,
    budget_controller: LayeredBudgetController | None = None,
) -> ReadOnlyPrimaryRuntime:
    snapshot = build_semantic_tool_snapshot(
        registry,
        scope=ManifestScope(
            permissions=context.permissions,
            project_ids=context.project_scope,
            include_bounded_loop_only=True,
            allowed_capability_ids=context.allowed_capability_ids,
            denied_capability_ids=context.denied_capability_ids,
        ),
    )
    ledger = SemanticEventLedger()
    gateway = SemanticToolGateway(
        snapshot=snapshot,
        executor=executor,
        context=context,
        ledger=ledger,
        mode=GatewayMode.EFFECT_PRIMARY,
        registry=registry,
        approval_service=approval_service,
        effect_operation_service=effect_operation_service,
        agent_plan_service=agent_plan_service,
        budget_controller=budget_controller,
    )
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        project_before_plan_mutation=True,
        require_interaction_assessment=False,
        mode=GatewayMode.EFFECT_PRIMARY,
        budget_controller=budget_controller,
    )
    # search_documentation is deliberately excluded from the model-facing set
    # here too (not just the read-only runtime): it overlaps with
    # answer_from_documentation closely enough that the model unreliably
    # picked the wrong one. Kept in the registry/gateway, not offered as a tool.
    model_facing_snapshot = snapshot.model_copy(
        update={
            "tools": tuple(
                record
                for record in snapshot.tools
                if record.canonical_id not in MODEL_FACING_READ_EXCLUSIONS
            )
        }
    )
    tools = build_semantic_structured_tools(model_facing_snapshot, gateway)
    background = json.dumps(background_context or {}, sort_keys=True, default=str)
    middlewares = [middleware] + ([LayeredModelBudgetMiddleware(budget_controller, model_name=str(getattr(model, "model_name", getattr(model, "model", ""))))] if budget_controller else [])
    graph = create_agent(
        model,
        tools,
        system_prompt=f"{EFFECT_PRIMARY_SYSTEM_PROMPT}\nAuthoritative background state: {background}",
        middleware=middlewares,
        interrupt_before=["tools"] if interrupt_before_tools else None,
        name="nexus_slice_5_effect_primary",
    )
    return ReadOnlyPrimaryRuntime(
        graph=graph,
        snapshot=snapshot,
        gateway=gateway,
        middleware=middleware,
        ledger=ledger,
        read_only_tools=tuple(item for item in snapshot.tools if item.governance_class.value == "read"),
        filtered_consequential=(),
    )


__all__ = ["EFFECT_PRIMARY_SYSTEM_PROMPT", "build_effect_primary_runtime"]
