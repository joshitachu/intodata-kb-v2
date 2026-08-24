from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import TURN_ASSESSMENT_TOOL_NAME
from src.harness.manifest import ManifestScope
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    PACK_CREATE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
    HarnessRiskTier,
)
from src.harness.run_budgets import (
    AgentBudgetStop,
    LayeredBudgetController,
    LayeredBudgetLimits,
)
from src.harness.registry import (
    CapabilityRegistry,
    base_capability_registry,
    default_capability_registry,
)
from src.harness.semantic_shadow_runtime import build_semantic_shadow_runtime
from src.harness.semantic_tools import (
    MAX_TOOL_MESSAGE_BYTES,
    SemanticSnapshotError,
    SemanticEventLedger,
    SemanticToolMiddleware,
    SemanticToolGateway,
    build_semantic_tool_snapshot,
    project_capability_observation,
    serialize_tool_envelope,
)
from src.harness import semantic_tools as semantic_tools_module


class ScriptedSemanticModel(FakeMessagesListChatModel):
    bound_schemas: list[dict[str, Any]] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        assert tool_choice in {"auto", "none"}
        self.bound_schemas = [tool.get_input_schema().model_json_schema() for tool in tools]
        return self


def _call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _calls(*calls: tuple[str, dict[str, Any], str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
            for name, args, call_id in calls
        ],
    )


def _scope() -> ManifestScope:
    return ManifestScope(
        include_bounded_loop_only=True,
        permissions={"manage_projects", "manage_packs"},
    )


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=7,
        conversation_id=11,
        project_scope=["fixture-project"],
        permissions={"manage_projects", "manage_packs"},
    )


def _pack_arguments(name: str) -> dict[str, Any]:
    """The create capability takes a brief; the pack author writes the manifest."""
    return {
        "name": name,
        "brief": (
            "Analyze production incidents and guide prevention decisions for "
            "platform engineers, covering incident timelines and root causes; "
            "produce an incident review report."
        ),
    }


def _tool_payloads(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(str(message.content))
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]


async def test_model_led_assessment_gates_consequential_tools_without_message_matching() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        require_interaction_assessment=True,
    )
    model_request = SimpleNamespace(
        messages=[HumanMessage(content="Anything can be written here")],
        tools=[object()],
    )
    model_request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def model_handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(model_request, model_handler) == (
        TURN_ASSESSMENT_TOOL_NAME
    )

    assessment_call = _call(
        TURN_ASSESSMENT_TOOL_NAME,
        {
            "mode": "prepare",
            "objective": "Guide preparation",
            "action_ready": False,
            "missing_information": ["the intended outcome"],
            "proposed_action": {
                "tool_name": "create_pack",
                "arguments": {"name": "Guided Pack"},
            },
            "clarification_to_user": "What outcome should I prepare this action for?",
        },
        "assessment-prepare",
    )
    await middleware.aafter_model({"messages": [assessment_call]}, None)

    async def assessment_handler(request):  # noqa: ANN001, ANN202
        return ToolMessage(
            content=json.dumps(
                {
                    "version": "1.0.0",
                    "status": "succeeded",
                    "summary": "Conversation stage assessed.",
                    "data": {},
                    "evidence_refs": [],
                    "retryable": False,
                    "evidence_state_relevant": False,
                }
            ),
            tool_call_id=request.tool_call["id"],
            name=TURN_ASSESSMENT_TOOL_NAME,
        )

    await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=assessment_call.tool_calls[0],
            state={
                "messages": [
                    HumanMessage(content="Help me prepare an action."),
                    assessment_call,
                ]
            },
        ),
        assessment_handler,
    )
    assert await middleware.awrap_model_call(model_request, model_handler) == "none"
    source = inspect.getsource(semantic_tools_module)
    assert "_EXPLICIT_NO_TOOL_AUTHORITY_PATTERNS" not in source
    assert "latest_user_forbids_tool_use" not in source


async def test_invalid_turn_assessment_gets_one_diagnostic_correction_then_stops() -> None:
    middleware = SemanticToolMiddleware(
        build_semantic_tool_snapshot(default_capability_registry(), scope=_scope()),
        SemanticEventLedger(),
        require_interaction_assessment=True,
        mode=GatewayMode.EFFECT_PRIMARY,
    )
    invalid_arguments = {
        "mode": "act",
        "objective": "Create a Pack",
        "action_ready": False,
        "missing_information": [],
        "proposed_action": {
            "tool_name": "create_pack",
            "arguments": {"name": "Talend Migration"},
        },
    }

    async def forbidden_handler(_request):  # noqa: ANN001, ANN202
        raise AssertionError("invalid assessment must not reach its tool handler")

    first = _call(TURN_ASSESSMENT_TOOL_NAME, invalid_arguments, "invalid-assessment-1")
    await middleware.aafter_model({"messages": [first]}, None)
    first_result = await middleware.awrap_tool_call(
        SimpleNamespace(tool_call=first.tool_calls[0], state={"messages": [first]}),
        forbidden_handler,
    )
    first_payload = json.loads(str(first_result.content))
    assert first_payload["status"] == "correction_required"
    assert first_payload["data"]["validation_errors"]

    second = _call(TURN_ASSESSMENT_TOOL_NAME, invalid_arguments, "invalid-assessment-2")
    await middleware.aafter_model({"messages": [second]}, None)
    second_result = await middleware.awrap_tool_call(
        SimpleNamespace(tool_call=second.tool_calls[0], state={"messages": [second]}),
        forbidden_handler,
    )
    second_payload = json.loads(str(second_result.content))
    assert second_payload["status"] == "failed"
    assert second_payload["data"]["validation_errors"]
    assert middleware.interaction_grounding_failed is True
    assert middleware.grounded_clarification is None

    model_request = SimpleNamespace(messages=[], tools=[object()])
    model_request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def model_handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(model_request, model_handler) == "none"


def test_clarification_fallback_text_is_not_present() -> None:
    source = inspect.getsource(semantic_tools_module)
    assert "_fallback_clarification" not in source
    assert "What additional detail should I use to complete this action?" not in source
    assert "What additional business detail should I use before proceeding?" not in source
    assert "Which missing detail would you like me to use for this action?" not in source
    assert "I need one additional business detail before I can safely proceed." not in source


async def test_planning_middleware_requires_projection_before_any_answer() -> None:
    snapshot = build_semantic_tool_snapshot(
        base_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(snapshot, ledger, required_projection=True)
    request = SimpleNamespace(
        messages=[HumanMessage(content="what's 1+1")],
        tools=[object()],
    )
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(request, handler) == "project_objective"
    assert ledger.events[-1].kind == "middleware.tool_choice_required"

    ledger.append(
        "gateway.executor_completed",
        semantic_name="project_objective",
        canonical_id="nexus.agent_plan.project_objective",
        observation_status="succeeded",
    )
    assert await middleware.awrap_model_call(request, handler) == "auto"


async def test_planning_middleware_skips_projection_for_discussion() -> None:
    snapshot = build_semantic_tool_snapshot(
        base_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        required_projection=True,
        require_interaction_assessment=True,
    )
    request = SimpleNamespace(
        messages=[HumanMessage(content="Hi")],
        tools=[object()],
    )
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def model_handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assessment_call = _call(
        TURN_ASSESSMENT_TOOL_NAME,
        {
            "mode": "discuss",
            "objective": "Reply to a greeting",
            "action_ready": False,
            "missing_information": [],
        },
        "assessment-greeting",
    )
    await middleware.aafter_model({"messages": [assessment_call]}, None)

    async def assessment_handler(tool_request):  # noqa: ANN001, ANN202
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "assessed"}),
            tool_call_id=tool_request.tool_call["id"],
            name=TURN_ASSESSMENT_TOOL_NAME,
        )

    await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=assessment_call.tool_calls[0],
            state={"messages": [assessment_call]},
        ),
        assessment_handler,
    )

    assert await middleware.awrap_model_call(request, model_handler) == "none"
    assert not any(
        event.canonical_id == "nexus.agent_plan.project_objective" for event in ledger.events
    )


async def test_planning_middleware_requires_plan_change_after_required_projection() -> None:
    snapshot = build_semantic_tool_snapshot(
        base_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        required_projection=True,
        required_plan_action="create_agent_plan",
        mode=GatewayMode.PLAN_CONTROL_PRIMARY,
    )
    request = SimpleNamespace(
        messages=[HumanMessage(content="Onboard then document the project")],
        tools=[object()],
    )
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    ledger.append(
        "gateway.executor_completed",
        semantic_name="project_objective",
        canonical_id="nexus.agent_plan.project_objective",
        observation_status="succeeded",
        plan_required=True,
    )
    assert await middleware.awrap_model_call(request, handler) == "create_agent_plan"

    ledger.append(
        "gateway.executor_completed",
        semantic_name="create_agent_plan",
        canonical_id="nexus.agent_plan.create_agent_plan",
        observation_status="succeeded",
    )
    assert await middleware.awrap_model_call(request, handler) == "auto"


async def test_effect_middleware_projects_before_first_plan_mutation() -> None:
    snapshot = build_semantic_tool_snapshot(
        base_capability_registry(),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        project_before_plan_mutation=True,
        mode=GatewayMode.EFFECT_PRIMARY,
    )
    await middleware.aafter_model(
        {
            "messages": [
                _call(
                    "create_agent_plan",
                    {
                        "goal": "Review then remediate",
                        "success_criteria": ["Risks reviewed"],
                        "constraints": [],
                        "scoped_entities": ["alitalend"],
                        "steps": [
                            {"key": "review", "objective": "Review", "kind": "read"},
                            {"key": "remediate", "objective": "Remediate", "kind": "effect"},
                        ],
                    },
                    "create-plan-1",
                )
            ]
        },
        None,
    )
    assert middleware._decisions["create-plan-1"] == "projection_required"
    request = SimpleNamespace(
        messages=[HumanMessage(content="Create a plan")],
        tools=[object()],
    )
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(request, handler) == "project_objective"

    ledger.append(
        "gateway.executor_completed",
        semantic_name="project_objective",
        canonical_id="nexus.agent_plan.project_objective",
        observation_status="succeeded",
        plan_required=True,
    )
    assert await middleware.awrap_model_call(request, handler) == "create_agent_plan"

    ledger.append(
        "gateway.executor_completed",
        semantic_name="create_agent_plan",
        canonical_id="nexus.agent_plan.create_agent_plan",
        observation_status="succeeded",
    )
    assert await middleware.awrap_model_call(request, handler) == "auto"


async def test_tool_limit_forces_answer_only_synthesis() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    controller = LayeredBudgetController(LayeredBudgetLimits(max_tool_calls=0))
    with pytest.raises(AgentBudgetStop):
        controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        snapshot,
        ledger,
        budget_controller=controller,
    )
    request = SimpleNamespace(
        messages=[ToolMessage(content="{}", tool_call_id="budget-stop")],
        tools=[object()],
    )
    request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(request, handler) == "none"
    assert ledger.events[-1].kind == "middleware.final_synthesis_only"


async def test_middleware_projects_argument_validation_without_raw_pydantic_text() -> None:
    class RequiredArguments(BaseModel):
        project_id: str

    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(snapshot, ledger)
    call = _call("list_project_documents", {}, "invalid-args-1")
    await middleware.aafter_model({"messages": [call]}, None)
    request = SimpleNamespace(
        tool_call=call.tool_calls[0],
        state={"messages": [call]},
    )

    async def handler(_request):  # noqa: ANN001, ANN202
        RequiredArguments.model_validate({})

    message = await middleware.awrap_tool_call(request, handler)
    payload = json.loads(str(message.content))

    assert payload["status"] == "needs_input"
    assert payload["summary"] == (
        "The tool needs missing or corrected information before it can run."
    )
    assert payload["retryable"] is True
    assert payload["data"]["validation_errors"] == [
        {
            "path": ["project_id"],
            "code": "missing",
            "message": "Required field is missing.",
        }
    ]
    assert payload["data"]["argument_shape"] == {}
    assert "field required" not in str(message.content).casefold()
    assert ledger.events[-1].kind == "middleware.needs_input"


async def test_middleware_needs_input_forces_response_only_without_retry() -> None:
    class RequiredArguments(BaseModel):
        project_id: str

    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    middleware = SemanticToolMiddleware(snapshot, SemanticEventLedger())

    async def handler(_request):  # noqa: ANN001, ANN202
        RequiredArguments.model_validate({})

    call = _call("list_project_documents", {}, "invalid-no-retry")
    await middleware.aafter_model({"messages": [call]}, None)
    request = SimpleNamespace(tool_call=call.tool_calls[0], state={"messages": [call]})
    message = await middleware.awrap_tool_call(request, handler)
    payload = json.loads(str(message.content))
    assert payload["status"] == "needs_input"
    assert payload["retryable"] is True

    model_request = SimpleNamespace(messages=[message], tools=[object()])
    model_request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def model_handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(model_request, model_handler) == "none"

    attempted = _call("create_pack", _pack_arguments("Must not run"), "ignored-tool-choice")
    await middleware.aafter_model({"messages": [attempted]}, None)
    executed = False

    async def forbidden_handler(_request):  # noqa: ANN001, ANN202
        nonlocal executed
        executed = True

    blocked = await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=attempted.tool_calls[0],
            state={"messages": [attempted]},
        ),
        forbidden_handler,
    )
    blocked_payload = json.loads(str(blocked.content))
    assert executed is False
    assert blocked_payload["status"] == "call_set_rejected"
    assert blocked_payload["error"]["code"] == "clarification_only"


async def test_middleware_does_not_restore_consequential_proposals_on_resume() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    middleware = SemanticToolMiddleware(snapshot, SemanticEventLedger())
    call = _call("create_pack", _pack_arguments("Unobserved"), "resumed-write")
    executed = False

    async def handler(_request):  # noqa: ANN001, ANN202
        nonlocal executed
        executed = True

    blocked = await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=call.tool_calls[0],
            state={"messages": [call]},
        ),
        handler,
    )
    payload = json.loads(str(blocked.content))
    assert executed is False
    assert payload["status"] == "denied"
    assert payload["error"]["code"] == "middleware_proposal_missing"


async def test_assessed_consequential_action_is_consumed_after_one_call() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    middleware = SemanticToolMiddleware(
        snapshot,
        SemanticEventLedger(),
        require_interaction_assessment=True,
        mode=GatewayMode.EFFECT_PRIMARY,
    )
    arguments = _pack_arguments("One Pack")
    assessment_call = _call(
        TURN_ASSESSMENT_TOOL_NAME,
        {
            "mode": "act",
            "objective": "Create one Pack",
            "action_ready": True,
            "missing_information": [],
            "proposed_action": {
                "tool_name": "create_pack",
                "arguments": arguments,
            },
        },
        "assessment-once",
    )
    await middleware.aafter_model({"messages": [assessment_call]}, None)

    async def assessment_handler(request):  # noqa: ANN001, ANN202
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "assessed"}),
            tool_call_id=request.tool_call["id"],
            name=TURN_ASSESSMENT_TOOL_NAME,
        )

    await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=assessment_call.tool_calls[0],
            state={"messages": [assessment_call]},
        ),
        assessment_handler,
    )

    executions = 0

    async def execute(request):  # noqa: ANN001, ANN202
        nonlocal executions
        executions += 1
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "created"}),
            tool_call_id=request.tool_call["id"],
            name="create_pack",
        )

    first = _call("create_pack", arguments, "create-once")
    await middleware.aafter_model({"messages": [first]}, None)
    await middleware.awrap_tool_call(
        SimpleNamespace(tool_call=first.tool_calls[0], state={"messages": [first]}),
        execute,
    )

    model_request = SimpleNamespace(messages=[], tools=[object()])
    model_request.override = lambda **kwargs: SimpleNamespace(**kwargs)

    async def model_handler(overridden):  # noqa: ANN001, ANN202
        return overridden.tool_choice

    assert await middleware.awrap_model_call(model_request, model_handler) == "none"

    second = _call("create_pack", arguments, "create-twice")
    await middleware.aafter_model({"messages": [second]}, None)
    blocked = await middleware.awrap_tool_call(
        SimpleNamespace(tool_call=second.tool_calls[0], state={"messages": [second]}),
        execute,
    )
    payload = json.loads(str(blocked.content))

    assert executions == 1
    assert payload["status"] == "call_set_rejected"
    assert payload["error"]["code"] == "conversation_action_consumed"


async def test_assessed_action_binds_prevalidated_arguments_when_model_call_drifts() -> None:
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(
        build_semantic_tool_snapshot(default_capability_registry(), scope=_scope()),
        ledger,
        require_interaction_assessment=True,
        mode=GatewayMode.EFFECT_PRIMARY,
    )
    assessed_arguments = _pack_arguments("Talend Migration")
    assessment_call = _call(
        TURN_ASSESSMENT_TOOL_NAME,
        {
            "mode": "act",
            "objective": "Create the named Pack",
            "action_ready": True,
            "missing_information": [],
            "proposed_action": {
                "tool_name": "create_pack",
                "arguments": assessed_arguments,
            },
        },
        "assessment-bind",
    )
    await middleware.aafter_model({"messages": [assessment_call]}, None)

    async def assessment_handler(request):  # noqa: ANN001, ANN202
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "assessed"}),
            tool_call_id=request.tool_call["id"],
            name=TURN_ASSESSMENT_TOOL_NAME,
        )

    await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=assessment_call.tool_calls[0],
            state={"messages": [assessment_call]},
        ),
        assessment_handler,
    )

    drifted = _call("create_pack", {}, "create-bound")
    await middleware.aafter_model({"messages": [drifted]}, None)
    executed_arguments = None

    async def execute(request):  # noqa: ANN001, ANN202
        nonlocal executed_arguments
        executed_arguments = dict(request.tool_call["args"])
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "created"}),
            tool_call_id=request.tool_call["id"],
            name="create_pack",
        )

    result = await middleware.awrap_tool_call(
        SimpleNamespace(
            tool_call=drifted.tool_calls[0],
            state={"messages": [drifted]},
        ),
        execute,
    )

    assert json.loads(str(result.content))["status"] == "succeeded"
    assert executed_arguments == assessed_arguments
    assert any(
        event.kind == "middleware.assessment_arguments_applied"
        and event.policy_class == "prevalidated_assessment_arguments"
        for event in ledger.events
    )


async def test_middleware_rejects_excessive_parallel_read_fanout_before_execution() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(snapshot, ledger)
    message = _calls(
        *(
            (
                "read_document",
                {"query": "job name", "project_id": "fixture-project", "document_id": index},
                f"read-fanout-{index}",
            )
            for index in range(1, 5)
        )
    )
    await middleware.aafter_model({"messages": [message]}, None)
    executed = False

    async def handler(_request):  # noqa: ANN001, ANN202
        nonlocal executed
        executed = True

    request = SimpleNamespace(
        tool_call=message.tool_calls[0],
        state={"messages": [message]},
    )
    projected = await middleware.awrap_tool_call(request, handler)
    payload = json.loads(str(projected.content))

    assert executed is False
    assert payload["status"] == "call_set_rejected"
    assert payload["error"]["code"] == "read_fanout_rejected"
    assert "at most three bounded reads" in payload["summary"]


async def test_middleware_deduplicates_identical_reads_before_second_execution() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    ledger = SemanticEventLedger()
    middleware = SemanticToolMiddleware(snapshot, ledger)
    executions = 0

    async def execute(request):  # noqa: ANN001, ANN202
        nonlocal executions
        executions += 1
        return ToolMessage(
            content=json.dumps({"status": "succeeded", "summary": "inventory"}),
            tool_call_id=request.tool_call["id"],
        )

    for call_id in ("inventory-first", "inventory-repeat"):
        message = _call("list_project_documents", {"project_id": "fixture-project"}, call_id)
        await middleware.aafter_model({"messages": [message]}, None)
        projected = await middleware.awrap_tool_call(
            SimpleNamespace(tool_call=message.tool_calls[0], state={"messages": [message]}),
            execute,
        )

    payload = json.loads(str(projected.content))
    assert executions == 1
    assert payload["status"] == "call_set_rejected"
    assert payload["error"]["code"] == "repeated_call"
    assert ledger.events[-1].kind == "middleware.repeated_read_rejected"


def test_semantic_snapshot_is_complete_stable_and_schema_identical() -> None:
    registry = default_capability_registry()
    first = build_semantic_tool_snapshot(registry, scope=_scope())
    second = build_semantic_tool_snapshot(registry, scope=_scope())

    assert len(first.tools) >= 24
    assert {
        "background_conversation_task",
        "pause_conversation_task",
        "resume_conversation_task",
    }.issubset(first.semantic_to_canonical)
    assert first.snapshot_hash == second.snapshot_hash
    assert first.semantic_to_canonical["read_conversation_summary"] == (
        CONVERSATION_SUMMARY_FACTS_CAPABILITY.id
    )
    assert first.canonical_to_semantic[CONVERSATION_SUMMARY_FACTS_CAPABILITY.id] == (
        "read_conversation_summary"
    )
    assert first.project_binding_authority == "request_capability_registry"
    assert first.handler_schema_output_parity is True
    for record in first.tools:
        capability = registry.get(record.canonical_id)
        assert record.input_schema == capability.input_schema
        assert record.description.startswith("Purpose:")
        assert "Safe recovery:" in record.description


def test_semantic_snapshot_rejects_collision_drift_and_open_schema() -> None:
    base = default_capability_registry()
    read = base.get(CONVERSATION_SUMMARY_FACTS_CAPABILITY.id)
    search = base.get("rag.docs.search_evidence")
    handler_read = base.handler_for(read.id)
    handler_search = base.handler_for(search.id)

    collision = search.model_copy(update={"semantic_name": read.semantic_name})
    collision_handler = _matching_handler(collision, handler_search)
    with pytest.raises(SemanticSnapshotError, match="collision"):
        build_semantic_tool_snapshot(
            CapabilityRegistry([read, collision], handlers=[handler_read, collision_handler]),
            scope=_scope(),
        )

    with pytest.raises(SemanticSnapshotError, match="drift"):
        build_semantic_tool_snapshot(
            CapabilityRegistry([read], handlers=[handler_read]),
            scope=_scope(),
            expected_names={read.id: "different_name"},
        )

    open_schema = read.model_copy(update={"input_schema": {"type": "object", "properties": {}}})
    with pytest.raises(SemanticSnapshotError, match="open object"):
        build_semantic_tool_snapshot(
            CapabilityRegistry(
                [open_schema], handlers=[_matching_handler(open_schema, handler_read)]
            ),
            scope=_scope(),
        )


def test_private_pack_capabilities_require_explicit_pack_authority() -> None:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=ManifestScope(
            include_bounded_loop_only=True,
            permissions={"query_rag", "view_assigned_projects"},
        ),
    )

    assert "save_pack" not in snapshot.semantic_to_canonical
    assert "create_pack" not in snapshot.semantic_to_canonical
    assert any(
        item.semantic_name == "save_pack" and item.reason_code == "permission_filtered"
        for item in snapshot.unavailable
    )


def test_snapshot_separates_read_evidence_from_execution_outcome_authority() -> None:
    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(),
        scope=_scope(),
    )
    read = snapshot.tool_for_name("search_documentation")
    write = snapshot.tool_for_name("create_pack")

    assert read is not None
    assert read.evidence_state_relevant is True
    assert read.execution_state_relevant is False
    assert write is not None
    assert write.evidence_state_relevant is False
    assert write.execution_state_relevant is True

    envelope = project_capability_observation(
        CapabilityObservation(
            capability_id=write.canonical_id,
            status="succeeded",
            summary="Created a private Analysis Pack.",
            receipt_ref={"idempotency_key": "pack-create-1"},
        ),
        capability=write,
    )
    assert envelope.evidence_state_relevant is False
    assert envelope.execution_state_relevant is True


def _matching_handler(capability, original):  # noqa: ANN001, ANN202
    class MatchingHandler:
        capability_id = capability.id
        input_schema = capability.input_schema
        output_type = capability.output_type
        task_types = tuple(capability.task_types)

        async def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return await original(*args, **kwargs)

    return MatchingHandler()


async def test_executor_draft_2020_validation_prevents_handler_call() -> None:
    calls = 0

    async def adapter(arguments, context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return EvidenceQueryResult(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            status="succeeded",
        )

    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: adapter},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            arguments={"max_messages": 21, "unexpected": True},
        ),
        _context(),
    )

    assert calls == 0
    assert observation.status == "needs_input"
    assert observation.retryable is True
    assert observation.error_code == "validation"
    assert observation.safe_details["field_path"] in {"$", "max_messages"}
    assert observation.error_message == "The capability arguments did not match the contract."


async def test_executor_exposes_stable_permission_turn_claim_and_unavailable_codes() -> None:
    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(registry)

    permission = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "fixture-project"},
        ),
        CapabilityExecutionContext(user_id=1, permissions=set()),
    )
    turn_claim = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments={},
        ),
        CapabilityExecutionContext(user_id=1, conversation_id=1),
    )
    unavailable_capability = CONVERSATION_SUMMARY_FACTS_CAPABILITY.model_copy(
        update={"enabled": False}
    )
    unavailable_registry = CapabilityRegistry(
        [unavailable_capability],
        handlers=[
            _matching_handler(
                unavailable_capability,
                registry.handler_for(CONVERSATION_SUMMARY_FACTS_CAPABILITY.id),
            )
        ],
    )
    unavailable = await HarnessCapabilityExecutor(unavailable_registry).execute(
        CapabilityExecutionRequest(
            capability_id=unavailable_capability.id,
            arguments={},
        ),
        CapabilityExecutionContext(user_id=1),
    )

    assert permission.error_code == "permission"
    assert turn_claim.error_code == "turn_claim"
    assert unavailable.error_code == "unavailable"


async def test_real_registered_summary_read_executes_once_through_executor() -> None:
    calls = 0

    async def adapter(arguments, context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        assert arguments == {"max_messages": 2}
        assert context.conversation_id == 11
        return EvidenceQueryResult(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="conversation:11:fact:1",
                    capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
                    source_type="conversation_summary",
                    title="Bounded fact",
                    summary="The current conversation concerns Slice 2.",
                )
            ],
        )

    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: adapter},
    )
    runtime = build_semantic_shadow_runtime(
        model=ScriptedSemanticModel(
            responses=[
                _call(
                    "read_conversation_summary",
                    {"max_messages": 2},
                    "call-summary-1",
                ),
                AIMessage(content="The bounded conversation fact was read."),
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Recap this conversation."}]}
    )

    assert calls == 1
    payloads = _tool_payloads(result)
    assert payloads[0]["status"] == "succeeded"
    assert payloads[0]["data"]["items"][0]["summary"].startswith("The current")
    kinds = [event.kind for event in runtime.ledger.events]
    assert kinds.index("middleware.intercepted") < kinds.index("gateway.executor_started")
    assert kinds.index("gateway.executor_started") < kinds.index("gateway.executor_completed")


async def test_uncertified_read_and_write_are_proposal_only() -> None:
    class NeverExecutor:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("shadow proposal reached executor")

    registry = default_capability_registry()
    runtime = build_semantic_shadow_runtime(
        model=ScriptedSemanticModel(
            responses=[
                _call(
                    "search_documentation",
                    {"query": "project summary", "project_id": "fixture-project"},
                    "call-read-proposal",
                ),
                _call(
                    "create_pack",
                    _pack_arguments("Shadow Pack"),
                    "call-write-proposal",
                ),
                AIMessage(content="Both were recorded without effects."),
            ]
        ),
        registry=registry,
        executor=NeverExecutor(),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Evaluate shadow proposals."}]}
    )

    payloads = _tool_payloads(result)
    assert [item["status"] for item in payloads] == ["proposal_only", "proposal_only"]
    assert all(set(item["data"]) == {"arguments_hash"} for item in payloads)


async def test_multi_consequential_call_set_rejects_every_call() -> None:
    class NeverExecutor:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("rejected call set reached executor")

    runtime = build_semantic_shadow_runtime(
        model=ScriptedSemanticModel(
            responses=[
                _calls(
                    ("create_pack", _pack_arguments("Rejected Pack"), "call-create"),
                    (
                        "save_pack",
                        {"draft_id": 1, "expected_revision": 1},
                        "call-save",
                    ),
                    (
                        "read_conversation_summary",
                        {"max_messages": 1},
                        "call-mixed-read",
                    ),
                ),
                AIMessage(content="The call set was rejected."),
            ]
        ),
        registry=default_capability_registry(),
        executor=NeverExecutor(),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Do all three."}]}
    )

    payloads = _tool_payloads(result)
    assert len(payloads) == 3
    assert {item["status"] for item in payloads} == {"call_set_rejected"}


async def test_multiple_shadow_safe_reads_are_serialized() -> None:
    class RecordingExecutor:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0
            self.calls = 0

        async def execute(self, request, context):  # noqa: ANN001
            self.calls += 1
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="succeeded",
                summary="Read complete.",
            )

    executor = RecordingExecutor()
    runtime = build_semantic_shadow_runtime(
        model=ScriptedSemanticModel(
            responses=[
                _calls(
                    (
                        "read_conversation_summary",
                        {"max_messages": 1},
                        "call-read-1",
                    ),
                    (
                        "read_conversation_summary",
                        {"max_messages": 2},
                        "call-read-2",
                    ),
                ),
                AIMessage(content="Both reads completed serially."),
            ]
        ),
        registry=default_capability_registry(),
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Read two bounded windows."}]}
    )

    assert executor.calls == 2
    assert executor.maximum == 1


def test_tool_projection_redacts_bounds_and_omits_raw_error() -> None:
    registry = default_capability_registry()
    snapshot = build_semantic_tool_snapshot(registry, scope=_scope())
    record = snapshot.tool_for_name("read_conversation_summary")
    assert record is not None
    observation = CapabilityObservation(
        capability_id=record.canonical_id,
        status="failed",
        summary="provider token=super-secret-token-123456789 failed",
        data={
            "authorization": "Bearer secret-value-123456789",
            "nested": {"password": "hidden", "values": ["x" * 10_000] * 100},
        },
        evidence_refs=[{"evidence_id": f"ref-{index}", "private": "drop"} for index in range(50)],
        error_code="raw_provider_exception",
        error_message="Traceback SQL SELECT * credential=leak-me",
        workflow_ref={"external_run_id": "run-1", "private": "drop"},
        receipt_ref={"idempotency_key": "receipt-1", "private": "drop"},
    )

    encoded = serialize_tool_envelope(
        project_capability_observation(observation, capability=record)
    )
    payload = json.loads(encoded)

    assert len(encoded.encode("utf-8")) <= MAX_TOOL_MESSAGE_BYTES
    assert "super-secret-token" not in encoded
    assert "Traceback" not in encoded
    assert "SELECT" not in encoded
    assert "leak-me" not in encoded
    assert payload["error"]["code"] == "handler_failure"
    assert len(payload["evidence_refs"]) == 10
    assert payload["workflow_ref"] == {"external_run_id": "run-1"}


def test_oversized_evidence_projection_preserves_complete_leading_passages() -> None:
    registry = default_capability_registry()
    snapshot = build_semantic_tool_snapshot(registry, scope=_scope())
    record = snapshot.tool_for_name("search_documentation")
    assert record is not None
    observation = CapabilityObservation(
        capability_id=record.canonical_id,
        status="succeeded",
        summary="Retrieved bounded passages.",
        data={
            "items": [
                {
                    "id": f"doc:392:{index}",
                    "title": "02_COMPONENT_INVENTORY.md",
                    "summary": f"passage-{index}:" + (" job-name" * 220),
                }
                for index in range(10)
            ]
        },
        evidence_refs=[{"evidence_id": f"doc:392:{index}"} for index in range(10)],
    )

    encoded = serialize_tool_envelope(
        project_capability_observation(observation, capability=record)
    )
    payload = json.loads(encoded)

    assert len(encoded.encode("utf-8")) <= MAX_TOOL_MESSAGE_BYTES
    assert payload["data"]["truncated"] is True
    assert 1 <= payload["data"]["returned_item_count"] < 10
    assert payload["data"]["total_item_count"] == 10
    assert payload["data"]["items"][0]["summary"].startswith("passage-0:")
    assert len(payload["data"]["items"][0]["summary"]) > 500


def test_gateway_has_no_model_supplied_authority_surface() -> None:
    fields = set(SemanticToolGateway.execute.__annotations__)
    assert fields == {"semantic_name", "arguments", "return"}


# --- Slice 3: additive GatewayMode (SHADOW default preserved, READ_ONLY_PRIMARY) ---

from src.harness.semantic_tools import (  # noqa: E402
    GatewayMode,
    _CURRENT_CALL_ID,
    _tool_timeout,
)


class _CountingExecutor:
    def __init__(self, observation: CapabilityObservation) -> None:
        self.calls: list[str] = []
        self._observation = observation

    async def execute(self, request, context):  # noqa: ANN001
        self.calls.append(request.capability_id)
        assert context.allow_writes_content is False
        assert context.allow_external_side_effects is False
        assert context.approvals == set()
        return self._observation.model_copy(update={"capability_id": request.capability_id})


async def _run_one(mode: GatewayMode, semantic_name: str, observation) -> tuple:  # noqa: ANN001
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    executor = _CountingExecutor(observation)
    gateway = SemanticToolGateway(
        snapshot=snapshot,
        executor=executor,
        context=_context(),
        mode=mode,
    )
    token = _CURRENT_CALL_ID.set("call-mode-test")
    try:
        envelope = await gateway.execute(semantic_name, {"query_type": "project_list"})
    finally:
        _CURRENT_CALL_ID.reset(token)
    return envelope, executor, gateway


def _metadata_observation() -> CapabilityObservation:
    return CapabilityObservation(
        capability_id="nexus.metadata.query",
        status="succeeded",
        evidence_refs=[{"evidence_id": "ev-1", "source_type": "metadata"}],
    )


def test_gateway_defaults_to_shadow_mode() -> None:
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    gateway = SemanticToolGateway(
        snapshot=snapshot, executor=_CountingExecutor(_metadata_observation()), context=_context()
    )
    assert gateway._mode is GatewayMode.SHADOW


async def test_shadow_mode_keeps_non_shadow_safe_read_proposal_only() -> None:
    envelope, executor, _ = await _run_one(
        GatewayMode.SHADOW, "query_project_metadata", _metadata_observation()
    )
    assert envelope.status == "proposal_only"
    assert executor.calls == []  # no execution in shadow for a non-certified read


async def test_readonly_primary_executes_non_shadow_safe_read_for_real() -> None:
    envelope, executor, gateway = await _run_one(
        GatewayMode.READ_ONLY_PRIMARY, "query_project_metadata", _metadata_observation()
    )
    assert envelope.status == "succeeded"
    assert executor.calls == ["nexus.metadata.query"]
    assert envelope.evidence_refs and envelope.evidence_refs[0]["evidence_id"] == "ev-1"
    started = [e for e in gateway.ledger.events if e.kind == "gateway.executor_started"]
    assert started[-1].policy_class == "read_only_primary_read"


async def test_plan_primary_executes_read_tools_for_real() -> None:
    envelope, executor, gateway = await _run_one(
        GatewayMode.PLAN_CONTROL_PRIMARY,
        "query_project_metadata",
        _metadata_observation(),
    )
    assert envelope.status == "succeeded"
    assert executor.calls == ["nexus.metadata.query"]
    assert envelope.evidence_refs and envelope.evidence_refs[0]["evidence_id"] == "ev-1"
    started = [e for e in gateway.ledger.events if e.kind == "gateway.executor_started"]
    assert started[-1].policy_class == "plan_control_primary_read"


async def test_readonly_primary_keeps_consequential_proposal_only() -> None:
    envelope, executor, _ = await _run_one(
        GatewayMode.READ_ONLY_PRIMARY,
        "create_pack",
        _metadata_observation(),
    )
    assert envelope.status == "proposal_only"
    assert executor.calls == []  # consequential handler never runs


def test_create_pack_uses_bounded_capability_timeout_without_handoff_classification() -> None:
    controller = LayeredBudgetController(
        LayeredBudgetLimits(
            write_tool_timeout_seconds=30,
            workflow_handoff_timeout_seconds=15,
        )
    )
    timeout = _tool_timeout(
        controller,
        capability=PACK_CREATE_CAPABILITY,
        risk_tier=PACK_CREATE_CAPABILITY.risk_tier,
        estimated_cost_usd=0,
        workflow_handoff=False,
    )

    assert PACK_CREATE_CAPABILITY.metadata["workflow_handoff"] is False
    assert PACK_CREATE_CAPABILITY.metadata["tool_timeout_seconds"] == 60
    assert timeout == 60


@pytest.mark.parametrize(
    ("capability", "risk_tier", "workflow_handoff", "expected"),
    [
        (
            DOCUMENTATION_GENERATION_CAPABILITY,
            HarnessRiskTier.WRITES_CONTENT,
            True,
            15,
        ),
        (
            CONVERSATION_SUMMARY_FACTS_CAPABILITY,
            HarnessRiskTier.READ_ONLY,
            False,
            20,
        ),
    ],
)
def test_capabilities_without_override_retain_configured_timeout(
    capability,
    risk_tier,
    workflow_handoff,
    expected,  # noqa: ANN001
) -> None:
    controller = LayeredBudgetController(
        LayeredBudgetLimits(
            read_tool_timeout_seconds=20,
            write_tool_timeout_seconds=30,
            workflow_handoff_timeout_seconds=15,
        )
    )

    assert (
        _tool_timeout(
            controller,
            capability=capability,
            risk_tier=risk_tier,
            estimated_cost_usd=0,
            workflow_handoff=workflow_handoff,
        )
        == expected
    )


@pytest.mark.parametrize(
    "invalid_override",
    [True, "60", 0, -1, 121, 10**1000, float("nan"), float("inf")],
)
def test_invalid_capability_timeout_override_safely_uses_configured_timeout(
    invalid_override,
) -> None:  # noqa: ANN001
    capability = PACK_CREATE_CAPABILITY.model_copy(
        update={
            "metadata": {
                **PACK_CREATE_CAPABILITY.metadata,
                "tool_timeout_seconds": invalid_override,
            }
        }
    )
    controller = LayeredBudgetController(LayeredBudgetLimits(write_tool_timeout_seconds=30))

    assert (
        _tool_timeout(
            controller,
            capability=capability,
            risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
            estimated_cost_usd=0,
            workflow_handoff=False,
        )
        == 30
    )


def test_capability_timeout_override_is_bounded_by_remaining_hard_deadline() -> None:
    now = [0.0]
    controller = LayeredBudgetController(
        LayeredBudgetLimits(
            write_tool_timeout_seconds=10,
            soft_deadline_seconds=90,
            hard_deadline_seconds=100,
        ),
        clock=lambda: now[0],
    )
    now[0] = 75

    assert (
        _tool_timeout(
            controller,
            capability=PACK_CREATE_CAPABILITY,
            risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
            estimated_cost_usd=0,
            workflow_handoff=False,
        )
        == 25
    )


async def test_shadow_safe_read_still_executes_in_shadow_mode() -> None:
    observation = CapabilityObservation(
        capability_id="nexus.conversations.read_summary_facts", status="succeeded"
    )
    snapshot = build_semantic_tool_snapshot(default_capability_registry(), scope=_scope())
    executor = _CountingExecutor(observation)
    gateway = SemanticToolGateway(
        snapshot=snapshot, executor=executor, context=_context(), mode=GatewayMode.SHADOW
    )
    token = _CURRENT_CALL_ID.set("call-shadow-safe")
    try:
        envelope = await gateway.execute("read_conversation_summary", {"max_messages": 1})
    finally:
        _CURRENT_CALL_ID.reset(token)
    assert envelope.status == "succeeded"
    assert executor.calls == ["nexus.conversations.read_summary_facts"]
    started = [e for e in gateway.ledger.events if e.kind == "gateway.executor_started"]
    assert started[-1].policy_class == "shadow_safe_read"
