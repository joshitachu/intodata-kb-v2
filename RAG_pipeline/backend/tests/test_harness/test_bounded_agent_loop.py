from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.harness import agent_loop as agent_loop_module
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentLoopInput,
    AgentLoopLimits,
    AgentLoopResult,
    AgentModelError,
    AgentModelResult,
    AgentStopReason,
    AgentUsage,
    BoundedAgentLoop,
    ProviderAgentDecisionModel,
    non_stream_transport,
    response_fingerprint,
    stream_transport,
    _provider_tool_name,
)
from src.harness.agent_loop_context import (
    build_authoritative_agent_input,
    enforce_canonical_repeat_guard,
    hydrate_authoritative_workflow_cards,
    persist_typed_agent_response,
)
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityObservation
from src.harness.conversation_state import ConversationState
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import (
    AssessedAction,
    ConversationMode,
    TURN_ASSESSMENT_TOOL_NAME,
    TurnAssessment,
)
from src.harness.models import (
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    PACK_CREATE_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    WORKFLOW_CANCELLATION_CAPABILITY,
    CapabilityDefinition,
    HarnessProviderType,
    HarnessRiskTier,
    WorkflowRunStatus,
)
from src.harness.providers.mcp_docs import (
    McpActiveProjectWorkflow,
    McpDocumentationGenerationStatus,
    McpDocumentationProviderError,
)
from src.harness.registry import CapabilityRegistry, base_capability_registry
from src.harness.response_synthesis import (
    ResponseFact,
    ResponseFactKind,
    ResponseSynthesisPlan,
    render_typed_response,
)
from src.harness.workflow_projection import WorkflowCard
from src.harness.workflow_control import WorkflowCancellationOutcome
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse


@dataclass
class ScriptedModel:
    supports_interaction_assessment = True

    results: list[AgentModelResult | Exception]
    calls: int = 0
    requests: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.requests = []

    async def decide(self, request):  # noqa: ANN001
        self.requests.append(request)
        if request.assessment_required:
            next_item = self.results[self.calls]
            if (
                isinstance(next_item, AgentModelResult)
                and next_item.decision.action is AgentBoundaryAction.ASSESS
            ):
                self.calls += 1
                return next_item
            ready = bool(
                isinstance(next_item, AgentModelResult)
                and next_item.decision.action is AgentBoundaryAction.CALL_CAPABILITY
            )
            return AgentModelResult(
                decision=AgentDecision(
                    action=AgentBoundaryAction.ASSESS,
                    assessment=TurnAssessment(
                        mode=ConversationMode.ACT if ready else ConversationMode.DISCUSS,
                        objective="Scripted test objective",
                        action_ready=ready,
                        proposed_action=(
                            AssessedAction(
                                tool_name=_provider_tool_name(
                                    next_item.decision.capability_id or ""
                                ),
                                arguments=next_item.decision.arguments,
                            )
                            if ready and isinstance(next_item, AgentModelResult)
                            else None
                        ),
                    ),
                )
            )
        item = self.results[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


class RecordingHandler:
    def __init__(
        self,
        capability: CapabilityDefinition,
        *,
        observation: CapabilityObservation | None = None,
    ) -> None:
        self.capability_id = capability.id
        self.input_schema = capability.input_schema
        self.output_type = capability.output_type
        self.task_types = tuple(capability.task_types)
        self.calls = 0
        self.observation = observation

    async def __call__(self, capability, request, context, services):  # noqa: ANN001
        del request, context, services
        self.calls += 1
        return self.observation or CapabilityObservation(
            capability_id=capability.id,
            status="succeeded",
            output_type=capability.output_type,
            summary="safe result",
        )


class ExplodingExecutor:
    calls = 0

    async def execute(self, request, context):  # noqa: ANN001
        del request, context
        self.calls += 1
        raise AssertionError("unknown calls must never reach an executor")


class FakeDocsProvider:
    def __init__(self) -> None:
        self.status_calls = 0
        self.active_calls = 0

    async def get_generation_status(self, *, project_id, access_token):  # noqa: ANN001
        del access_token
        self.status_calls += 1
        return McpDocumentationGenerationStatus(
            project_id=project_id,
            found=True,
            run_id=91,
            status="running",
            started_at="2026-07-18T20:00:00Z",
            completed_at=None,
            doc_count=0,
            quality_score=None,
            run_verdict=None,
            client_ready=None,
            failed_gates=[],
            error_message=None,
            status_path=f"/projects/{project_id}/status",
            docs_path=f"/projects/{project_id}/docs",
        )

    async def list_active_project_workflows(self, *, allowed_project_ids, access_token):  # noqa: ANN001
        del access_token
        self.active_calls += 1
        project_id = (allowed_project_ids or ["alpha"])[0]
        return [
            McpActiveProjectWorkflow(
                project_id=project_id,
                display_name="Alpha",
                project_status="ready",
                generation_status="running",
                generation_started_at="2026-07-18T20:00:00Z",
                status_path=f"/projects/{project_id}/status",
                docs_path=f"/projects/{project_id}/docs",
            )
        ]


class FailingDocsProvider:
    calls = 0

    async def get_generation_status(self, *, project_id, access_token):  # noqa: ANN001
        del project_id, access_token
        self.calls += 1
        raise McpDocumentationProviderError("The status provider is temporarily unavailable.")


def decision(
    action: AgentBoundaryAction,
    *,
    content: str | None = None,
    capability_id: str | None = None,
    arguments: dict | None = None,
    cost: float = 0.0,
) -> AgentModelResult:
    return AgentModelResult(
        decision=AgentDecision(
            action=action,
            content=content,
            capability_id=capability_id,
            arguments=arguments or {},
        ),
        usage=AgentUsage(input_tokens=10, output_tokens=4, model_cost_usd=cost),
    )


def assessment(
    mode: ConversationMode,
    *,
    action_ready: bool = False,
    missing_information: list[str] | None = None,
    capability_id: str | None = None,
    arguments: dict | None = None,
    clarification_to_user: str | None = None,
) -> AgentModelResult:
    return AgentModelResult(
        decision=AgentDecision(
            action=AgentBoundaryAction.ASSESS,
            assessment=TurnAssessment(
                mode=mode,
                objective="Handle the current conversation appropriately",
                action_ready=action_ready,
                missing_information=missing_information or [],
                proposed_action=(
                    AssessedAction(
                        tool_name=_provider_tool_name(capability_id),
                        arguments=arguments or {},
                    )
                    if capability_id is not None
                    else None
                ),
                clarification_to_user=clarification_to_user,
            ),
        )
    )


def pack_arguments(name: str) -> dict:
    """The create capability takes a brief, not a structured artifact."""
    return {
        "name": name,
        "brief": (
            "Analyze production incidents and guide prevention decisions for "
            "platform engineers; produce an incident review report."
        ),
    }


async def test_prepare_mode_guides_without_creating_a_pack_draft() -> None:
    handler = RecordingHandler(PACK_CREATE_CAPABILITY)
    model = ScriptedModel(
        [
            assessment(
                ConversationMode.PREPARE,
                missing_information=["the Pack purpose"],
                capability_id=PACK_CREATE_CAPABILITY.id,
                arguments={"name": "Guided Pack"},
                clarification_to_user="What should this Pack help its audience decide?",
            ),
            decision(
                AgentBoundaryAction.CLARIFY,
                content="What should this Pack help its audience decide?",
            ),
        ]
    )
    loop, _ = loop_for(
        model,
        capability=PACK_CREATE_CAPABILITY,
        handler=handler,
    )

    result = await loop.run(
        AgentLoopInput(
            user_message="Okay, help and guide me through it. What do you need from me?"
        ),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.CLARIFICATION_REQUIRED
    assert result.output == "What should this Pack help its audience decide?"
    assert handler.calls == 0
    assert model.requests[1].manifest.tools


async def test_explain_mode_does_not_retry_the_prior_pack_action() -> None:
    handler = RecordingHandler(PACK_CREATE_CAPABILITY)
    model = ScriptedModel(
        [
            assessment(ConversationMode.EXPLAIN),
            decision(
                AgentBoundaryAction.ANSWER,
                content="The earlier request was incomplete, so no new action ran.",
            ),
        ]
    )
    loop, _ = loop_for(
        model,
        capability=PACK_CREATE_CAPABILITY,
        handler=handler,
    )

    result = await loop.run(
        AgentLoopInput(
            user_message="What does that mean?",
            conversation_history=[
                {
                    "role": "assistant",
                    "content": "The previous capability arguments were incomplete.",
                }
            ],
        ),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert handler.calls == 0
    assert model.requests[1].manifest.tools


async def test_action_assessment_cannot_authorize_different_pack_arguments() -> None:
    handler = RecordingHandler(PACK_CREATE_CAPABILITY)
    model = ScriptedModel(
        [
            assessment(
                ConversationMode.ACT,
                action_ready=True,
                capability_id=PACK_CREATE_CAPABILITY.id,
                arguments=pack_arguments("First Pack"),
            ),
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=PACK_CREATE_CAPABILITY.id,
                arguments=pack_arguments("Different Pack"),
            ),
        ]
    )
    loop, _ = loop_for(
        model,
        capability=PACK_CREATE_CAPABILITY,
        handler=handler,
    )

    result = await loop.run(
        AgentLoopInput(user_message="Create the First Pack now."),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.INVALID_MODEL_OUTPUT
    assert handler.calls == 0


async def test_invalid_arguments_need_input_then_ask_one_natural_clarification() -> None:
    capability = read_capability()
    handler = RecordingHandler(capability)
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={},
            ),
            decision(
                AgentBoundaryAction.CLARIFY,
                content="Which project should I check?",
            ),
        ]
    )
    loop, _ = loop_for(model, capability=capability, handler=handler)

    result = await loop.run(
        AgentLoopInput(user_message="Check the project."),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.CLARIFICATION_REQUIRED
    assert result.output == "Which project should I check?"
    assert result.observations[0].status == "needs_input"
    assert result.observations[0].safe_details["field_path"] == "project_id"
    assert model.requests[1].clarification_only is True
    assert model.requests[1].manifest.tools
    assert handler.calls == 0


async def test_needs_input_blocks_a_model_attempted_same_turn_retry() -> None:
    capability = read_capability()
    handler = RecordingHandler(capability)
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={},
            ),
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            ),
        ]
    )
    loop, _ = loop_for(model, capability=capability, handler=handler)

    result = await loop.run(
        AgentLoopInput(user_message="Check the project."),
        context=context(project_scope=["alpha"]),
    )

    assert result.stop_reason is AgentStopReason.INVALID_MODEL_OUTPUT
    assert [item.status.value for item in result.observations] == ["needs_input"]
    assert handler.calls == 0


def read_capability(
    *, capability_id: str = "test.read", estimated_cost: float = 0.0
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        label="Read test state",
        provider_type=HarnessProviderType.SOURCE,
        provider_id="test",
        action="read",
        input_schema={
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        output_type="test_result",
        risk_tier=HarnessRiskTier.READ_ONLY,
        task_types=["project_workflow_status"],
        metadata={"estimated_cost_usd": estimated_cost},
    )


def loop_for(
    model: ScriptedModel,
    *,
    capability: CapabilityDefinition | None = None,
    handler: RecordingHandler | None = None,
    executor=None,  # noqa: ANN001
    limits: AgentLoopLimits | None = None,
) -> tuple[BoundedAgentLoop, RecordingHandler | None]:
    capability = capability or read_capability()
    handler = handler or RecordingHandler(capability)
    registry = CapabilityRegistry(
        [capability],
        handlers=[handler],
    )
    return (
        BoundedAgentLoop(
            registry=registry,
            executor=executor or HarnessCapabilityExecutor(registry),
            model=model,
            limits=limits,
        ),
        handler,
    )


def context(*, project_scope: list[str] | None = None) -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=7,
        conversation_id=13,
        project_scope=project_scope or ["alpha"],
    )


@pytest.mark.parametrize(
    ("action", "expected_reason"),
    [
        (AgentBoundaryAction.ANSWER, AgentStopReason.ANSWERED),
        (AgentBoundaryAction.CLARIFY, AgentStopReason.CLARIFICATION_REQUIRED),
    ],
)
async def test_answer_and_clarify_are_terminal_boundary_actions(
    action: AgentBoundaryAction, expected_reason: AgentStopReason
) -> None:
    model = ScriptedModel([decision(action, content="A safe response")])
    loop, handler = loop_for(model)

    result = await loop.run(AgentLoopInput(user_message="hello"), context=context())

    assert result.action is action
    assert result.stop_reason is expected_reason
    assert result.output == "A safe response"
    assert handler is not None and handler.calls == 0
    assert [item.sequence for item in result.trace] == [1]


async def test_registered_status_observation_drives_the_next_model_answer() -> None:
    provider = FakeDocsProvider()
    registry = base_capability_registry()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
                arguments={"project_id": "alpha"},
            ),
            decision(AgentBoundaryAction.ANSWER, content="Alpha is still generating."),
        ]
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry, docs_provider=provider),
        model=model,
    )

    result = await loop.run(
        AgentLoopInput(
            user_message="What is happening?",
            workflow_state=[{"workflow_run_id": 44, "status": "running"}],
        ),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert provider.status_calls == 1
    assert model.requests[0].observations == []
    assert model.requests[2].observations[0].data["external_run_id"] == 91
    assert [item.action for item in result.trace] == [
        AgentBoundaryAction.ASSESS,
        AgentBoundaryAction.CALL_CAPABILITY,
        AgentBoundaryAction.ANSWER,
    ]


async def test_observation_can_drive_a_second_different_registered_call() -> None:
    provider = FakeDocsProvider()
    registry = base_capability_registry()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
                arguments={"project_id": "alpha"},
            ),
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id,
                arguments={},
            ),
            decision(
                AgentBoundaryAction.ANSWER,
                content="The generation is running and is the active workflow.",
            ),
        ]
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry, docs_provider=provider),
        model=model,
    )

    result = await loop.run(
        AgentLoopInput(user_message="Check it and tell me what remains active."),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert provider.status_calls == provider.active_calls == 1
    assert len(model.requests[2].observations) == 1
    assert len(model.requests[3].observations) == 2


async def test_registered_provider_failure_is_observed_then_safely_answered() -> None:
    provider = FailingDocsProvider()
    registry = base_capability_registry()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
                arguments={"project_id": "alpha"},
            ),
            decision(
                AgentBoundaryAction.CLARIFY,
                content="I couldn't refresh status. Would you like me to try again later?",
            ),
        ]
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry, docs_provider=provider),
        model=model,
    )

    result = await loop.run(
        AgentLoopInput(user_message="Refresh the status."),
        context=context(),
    )

    assert provider.calls == 1
    assert model.requests[2].observations[0].status == "failed"
    assert model.requests[2].observations[0].retryable is True
    assert result.stop_reason is AgentStopReason.CLARIFICATION_REQUIRED
    assert "plan_harness_chat_turn" not in inspect.getsource(agent_loop_module)


async def test_unknown_call_stops_typed_without_executor_or_legacy_routing() -> None:
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id="unknown.capability",
                arguments={},
            )
        ]
    )
    executor = ExplodingExecutor()
    loop, _ = loop_for(model, executor=executor)

    result = await loop.run(AgentLoopInput(user_message="do it"), context=context())

    assert result.stop_reason is AgentStopReason.UNKNOWN_CAPABILITY
    assert executor.calls == 0
    source = inspect.getsource(agent_loop_module)
    assert "plan_harness_chat_turn" not in source
    assert "plan_model_harness_chat_turn" not in source


async def test_denied_call_stops_and_policy_prevents_handler_invocation() -> None:
    capability = read_capability()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "outside"},
            )
        ]
    )
    loop, handler = loop_for(model, capability=capability)

    result = await loop.run(
        AgentLoopInput(user_message="read outside"), context=context(project_scope=["alpha"])
    )

    assert result.stop_reason is AgentStopReason.CAPABILITY_DENIED
    assert result.observations[0].status == "denied"
    assert handler is not None and handler.calls == 0


async def test_terminal_denial_streams_the_same_observation_exactly_once() -> None:
    capability = read_capability()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "outside"},
            )
        ]
    )
    loop, handler = loop_for(model, capability=capability)
    result = await loop.run(
        AgentLoopInput(user_message="read outside"),
        context=context(project_scope=["alpha"]),
    )

    non_stream = non_stream_transport(result)
    stream = [event async for event in stream_transport(result)]
    streamed_observations = [
        json.loads(data) for kind, data in stream if kind == "agent_observation"
    ]

    assert result.stop_reason is AgentStopReason.CAPABILITY_DENIED
    assert streamed_observations == non_stream["observations"]
    assert len(streamed_observations) == 1
    assert streamed_observations[0]["status"] == "denied"
    assert stream[-1][0] == "agent_final"
    assert handler is not None and handler.calls == 0


async def test_manifest_and_policy_share_server_owned_permissions() -> None:
    capability = read_capability().model_copy(
        update={"required_permissions": ["read_private_state"]}
    )
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            )
        ]
    )
    loop, handler = loop_for(model, capability=capability)

    result = await loop.run(
        AgentLoopInput(user_message="read"),
        context=CapabilityExecutionContext(
            user_id=7,
            conversation_id=13,
            project_scope=["alpha"],
            permissions=set(),
        ),
    )

    assert model.requests[0].manifest.tools == []
    assert result.stop_reason is AgentStopReason.CAPABILITY_DENIED
    assert handler is not None and handler.calls == 0


async def test_loop_never_broadens_caller_capability_allowlist() -> None:
    capability = read_capability()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            )
        ]
    )
    loop, handler = loop_for(model, capability=capability)

    result = await loop.run(
        AgentLoopInput(user_message="read"),
        context=CapabilityExecutionContext(
            user_id=7,
            conversation_id=13,
            project_scope=["alpha"],
            allowed_capability_ids=set(),
        ),
    )

    assert model.requests[0].manifest.tools == []
    assert result.stop_reason is AgentStopReason.CAPABILITY_DENIED
    assert handler is not None and handler.calls == 0


@pytest.mark.parametrize(
    "error",
    [
        AgentModelError(AgentStopReason.INVALID_MODEL_OUTPUT),
        RuntimeError("provider offline"),
    ],
)
async def test_model_failure_is_safe_and_never_calls_a_semantic_fallback(error: Exception) -> None:
    model = ScriptedModel([error])
    loop, handler = loop_for(model)

    result = await loop.run(AgentLoopInput(user_message="document maybe"), context=context())

    assert result.stop_reason in {
        AgentStopReason.INVALID_MODEL_OUTPUT,
        AgentStopReason.PLANNER_UNAVAILABLE,
    }
    assert handler is not None and handler.calls == 0
    assert result.output in {
        "Agent turn failed (invalid_model_output).",
        "Agent turn failed (planner_unavailable).",
    }


async def test_provider_adapter_no_tool_support_is_a_typed_planner_failure(monkeypatch) -> None:
    async def unavailable(messages, tools):  # noqa: ANN001
        del messages, tools
        return ToolAwareLLMResponse(provider="offline", no_tool_support=True)

    monkeypatch.setattr(agent_loop_module, "generate_answer_with_runtime_tools", unavailable)
    registry = base_capability_registry()
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=ProviderAgentDecisionModel(),
    )

    result = await loop.run(AgentLoopInput(user_message="status"), context=context())

    assert result.stop_reason is AgentStopReason.PLANNER_UNAVAILABLE
    assert result.observations == []


async def test_provider_adapter_retries_one_rejected_structured_decision(monkeypatch) -> None:
    calls = []

    async def respond(messages, tools, images=None):  # noqa: ANN001
        del images
        calls.append(messages)
        response_tool = next(tool for tool in tools if tool.name == "nexus_agent_respond")
        if len(calls) == 1:
            return ToolAwareLLMResponse(provider="kimi", text="unstructured response")
        return ToolAwareLLMResponse(
            provider="kimi",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="response-after-retry",
                    tool_name=response_tool.name,
                    arguments={"action": "answer", "content": "Recovered safely."},
                )
            ],
        )

    monkeypatch.setattr(agent_loop_module, "generate_answer_with_runtime_tools", respond)
    registry = CapabilityRegistry(
        [read_capability()],
        handlers=[RecordingHandler(read_capability())],
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=ProviderAgentDecisionModel(),
    )

    result = await loop.run(AgentLoopInput(user_message="answer"), context=context())

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert result.output == "Recovered safely."
    assert len(calls) == 2
    assert "previous provider response was rejected" in calls[1][-1]["content"].lower()


async def test_provider_adapter_uses_native_response_tool_and_authoritative_manifest(
    monkeypatch,
) -> None:
    captured = {}

    async def respond(messages, tools, images=None):  # noqa: ANN001
        del images
        if len(tools) == 1 and tools[0].name == TURN_ASSESSMENT_TOOL_NAME:
            captured["assessment_messages"] = messages
            captured["assessment_tools"] = tools
            return ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[
                    RuntimeToolCallRequest(
                        call_id="assessment-1",
                        tool_name=TURN_ASSESSMENT_TOOL_NAME,
                        arguments={
                            "mode": "discuss",
                            "objective": "Explain Pack creation",
                            "action_ready": False,
                            "missing_information": [],
                        },
                    )
                ],
            )
        captured["messages"] = messages
        captured["tools"] = tools
        response_tool = next(tool for tool in tools if tool.name == "nexus_agent_respond")
        return ToolAwareLLMResponse(
            provider="kimi",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="response-1",
                    tool_name=response_tool.name,
                    arguments={
                        "action": "answer",
                        "content": "Yes. I can create a private Analysis Pack draft here.",
                    },
                )
            ],
        )

    monkeypatch.setattr(agent_loop_module, "generate_answer_with_runtime_tools", respond)
    handler = RecordingHandler(PACK_CREATE_CAPABILITY)
    registry = CapabilityRegistry(
        [PACK_CREATE_CAPABILITY],
        handlers=[handler],
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=ProviderAgentDecisionModel(),
    )

    result = await loop.run(
        AgentLoopInput(user_message="Can I create a pack?"),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert result.output.startswith("Yes.")
    assert handler.calls == 0
    assert [tool.name for tool in captured["assessment_tools"]] == [
        TURN_ASSESSMENT_TOOL_NAME
    ]
    assert "Conversation-action protocol" in captured["assessment_messages"][0]["content"]
    assert PACK_CREATE_CAPABILITY.id in captured["assessment_messages"][0]["content"]
    assert PACK_CREATE_CAPABILITY.id in captured["messages"][0]["content"]
    assert '"input_schema"' in captured["messages"][0]["content"]


async def test_provider_adapter_calls_pack_create_then_renders_observation_without_fixed_lead(
    monkeypatch,
) -> None:
    call_count = 0

    async def respond(messages, tools, images=None):  # noqa: ANN001
        nonlocal call_count
        del messages, images
        call_count += 1
        if call_count == 1:
            return ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[
                    RuntimeToolCallRequest(
                        call_id="assessment-pack-create",
                        tool_name=TURN_ASSESSMENT_TOOL_NAME,
                        arguments={
                            "mode": "act",
                            "objective": "Create the requested Pack draft",
                            "action_ready": True,
                            "missing_information": [],
                            "proposed_action": {
                                "tool_name": _provider_tool_name(
                                    PACK_CREATE_CAPABILITY.id
                                ),
                                "arguments": pack_arguments("My Pack"),
                            },
                        },
                    )
                ],
            )
        if call_count == 2:
            capability_tool = next(tool for tool in tools if tool.name != "nexus_agent_respond")
            return ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[
                    RuntimeToolCallRequest(
                        call_id="pack-create-1",
                        tool_name=capability_tool.name,
                        arguments=pack_arguments("My Pack"),
                    )
                ],
            )
        response_tool = next(tool for tool in tools if tool.name == "nexus_agent_respond")
        fact_ids = response_tool.input_schema["properties"]["fact_ids"]["items"]["enum"]
        return ToolAwareLLMResponse(
            provider="kimi",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="response-2",
                    tool_name=response_tool.name,
                    arguments={"action": "answer", "fact_ids": fact_ids},
                )
            ],
        )

    monkeypatch.setattr(agent_loop_module, "generate_answer_with_runtime_tools", respond)
    handler = RecordingHandler(PACK_CREATE_CAPABILITY)
    registry = CapabilityRegistry(
        [PACK_CREATE_CAPABILITY],
        handlers=[handler],
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=ProviderAgentDecisionModel(),
    )

    result = await loop.run(
        AgentLoopInput(user_message="Create a Pack named My Pack now."),
        context=context().model_copy(
            update={
                "current_message_id": 99,
                "conversation_state_version": 1,
                "conversation_turn_claim_id": 7,
                "conversation_turn_claim_token": "test-claim",
            }
        ),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert handler.calls == 1
    assert "safe result" in result.output
    assert "current update" not in result.output.lower()


async def test_max_iterations_stops_after_one_observation() -> None:
    capability = read_capability()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            )
        ]
    )
    loop, handler = loop_for(
        model,
        capability=capability,
        limits=AgentLoopLimits(max_iterations=1),
    )

    result = await loop.run(AgentLoopInput(user_message="read"), context=context())

    assert result.stop_reason is AgentStopReason.MAX_ITERATIONS
    assert handler is not None and handler.calls == 1


async def test_max_tool_calls_stops_before_execution() -> None:
    capability = read_capability()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            )
        ]
    )
    loop, handler = loop_for(
        model,
        capability=capability,
        limits=AgentLoopLimits(max_tool_calls=0),
    )

    result = await loop.run(AgentLoopInput(user_message="read"), context=context())

    assert result.stop_reason is AgentStopReason.MAX_TOOL_CALLS
    assert handler is not None and handler.calls == 0


async def test_deadline_stops_a_slow_model() -> None:
    class SlowModel:
        async def decide(self, request):  # noqa: ANN001
            del request
            await asyncio.sleep(0.05)
            return decision(AgentBoundaryAction.ANSWER, content="too late")

    capability = read_capability()
    handler = RecordingHandler(capability)
    registry = CapabilityRegistry(
        [capability],
        handlers=[handler],
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=SlowModel(),
        limits=AgentLoopLimits(deadline_seconds=0.01),
    )

    result = await loop.run(AgentLoopInput(user_message="wait"), context=context())

    assert result.stop_reason is AgentStopReason.DEADLINE_EXCEEDED
    assert handler.calls == 0


async def test_deadline_stops_a_slow_registered_handler() -> None:
    capability = read_capability()

    class SlowHandler(RecordingHandler):
        async def __call__(self, capability, request, context, services):  # noqa: ANN001
            del capability, request, context, services
            self.calls += 1
            await asyncio.sleep(0.05)
            return CapabilityObservation(capability_id=self.capability_id, status="succeeded")

    handler = SlowHandler(capability)
    registry = CapabilityRegistry(
        [capability],
        handlers=[handler],
    )
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            )
        ]
    )
    loop = BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=model,
        limits=AgentLoopLimits(deadline_seconds=0.01),
    )

    result = await loop.run(AgentLoopInput(user_message="read"), context=context())

    assert result.stop_reason is AgentStopReason.DEADLINE_EXCEEDED
    assert handler.calls == 1
    assert result.observations == []


@pytest.mark.parametrize("cost_source", ["model", "capability"])
async def test_cost_budget_stops_before_unsafe_progress(cost_source: str) -> None:
    capability = read_capability(estimated_cost=0.20 if cost_source == "capability" else 0.0)
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
                cost=0.20 if cost_source == "model" else 0.0,
            )
        ]
    )
    loop, handler = loop_for(
        model,
        capability=capability,
        limits=AgentLoopLimits(max_cost_usd=0.10),
    )

    result = await loop.run(AgentLoopInput(user_message="read"), context=context())

    assert result.stop_reason is AgentStopReason.COST_BUDGET_EXCEEDED
    assert handler is not None and handler.calls == 0


async def test_canonical_repeated_call_stops_before_second_handler_call() -> None:
    capability = read_capability()
    first = decision(
        AgentBoundaryAction.CALL_CAPABILITY,
        capability_id=capability.id,
        arguments={"project_id": "alpha", "limit": 1},
    )
    reordered = decision(
        AgentBoundaryAction.CALL_CAPABILITY,
        capability_id=capability.id,
        arguments={"limit": 1, "project_id": "alpha"},
    )
    model = ScriptedModel([first, reordered])
    loop, handler = loop_for(model, capability=capability)

    result = await loop.run(AgentLoopInput(user_message="read twice"), context=context())

    assert result.stop_reason is AgentStopReason.REPEATED_CALL
    assert handler is not None and handler.calls == 1
    assert len(result.observations) == 1


@pytest.mark.parametrize("action", [AgentBoundaryAction.ANSWER, AgentBoundaryAction.CLARIFY])
async def test_identical_state_neutral_response_is_stopped(
    action: AgentBoundaryAction,
) -> None:
    state_version = "same-state"
    prior = response_fingerprint(
        action,
        "No change.",
        state_version=state_version,
        observations=[],
    )
    model = ScriptedModel([decision(action, content="No change.")])
    loop, _ = loop_for(model)

    result = await loop.run(
        AgentLoopInput(
            user_message="again",
            state_version=state_version,
            prior_response_fingerprints={prior},
        ),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.REPEATED_RESPONSE
    assert result.output == "Agent turn failed (repeated_response)."
    assert result.typed_response is not None
    assert result.typed_response.kind == "no_progress"


async def test_conversation_163_shape_runs_status_handler_through_bounded_loop() -> None:
    registry = base_capability_registry()
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id,
                arguments={},
            ),
            decision(AgentBoundaryAction.ANSWER, content="Which time frame?"),
        ]
    )
    result = await BoundedAgentLoop(
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            docs_provider=FakeDocsProvider(),
        ),
        model=model,
    ).run(
        AgentLoopInput(
            user_message="which project is currently having documents made",
            conversation_state={},
            workflow_state=[],
        ),
        context=CapabilityExecutionContext(user_id=1, project_scope=["alpha"]),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert result.observations[0].capability_id == (PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id)
    assert "Alpha" in result.output
    assert "Documentation status: Running" in result.output
    assert "time frame" not in result.output


async def test_conversation_166_cancel_terminal_result_replays_as_no_progress() -> None:
    class CancellationController:
        calls = 0

        async def cancel(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                outcome=WorkflowCancellationOutcome.ALREADY_FAILED,
                message="The workflow had already failed.",
                run=SimpleNamespace(
                    id=44,
                    project_id="IntodataStreams",
                    status=WorkflowRunStatus.FAILED,
                ),
            )

    registry = base_capability_registry()
    controller = CancellationController()

    async def execute_once(prior: set[str] | None = None):
        return await BoundedAgentLoop(
            registry=registry,
            executor=HarnessCapabilityExecutor(
                registry,
                workflow_cancellation_controller=controller,
            ),
            model=ScriptedModel(
                [
                    decision(
                        AgentBoundaryAction.CALL_CAPABILITY,
                        capability_id=WORKFLOW_CANCELLATION_CAPABILITY.id,
                        arguments={"workflow_reference": "IntodataStreams"},
                    ),
                    decision(
                        AgentBoundaryAction.ANSWER,
                        content="Choose the pending workflow action shown above.",
                    ),
                ]
            ),
        ).run(
            AgentLoopInput(
                user_message="cancel IntodataStreams",
                conversation_state={},
                workflow_state=[],
                state_version="cancel-terminal",
                prior_response_fingerprints=prior or set(),
            ),
            context=CapabilityExecutionContext(
                user_id=1,
                conversation_id=166,
                project_scope=["IntodataStreams"],
                approvals={WORKFLOW_CANCELLATION_CAPABILITY.id},
            ),
        )

    first = await execute_once()
    assert first.stop_reason is AgentStopReason.ANSWERED
    assert "already failed" in first.output
    assert "Workflow status: Failed" in first.output
    assert "pending workflow action" not in first.output
    assert first.response_fingerprint is not None

    replay = await execute_once({first.response_fingerprint})
    assert replay.stop_reason is AgentStopReason.REPEATED_RESPONSE
    assert replay.typed_response is not None
    assert replay.typed_response.kind == "no_progress"
    assert controller.calls == 2


async def test_changed_observation_allows_same_text_to_make_progress() -> None:
    capability = read_capability()
    state_version = "same-state"
    prior = response_fingerprint(
        AgentBoundaryAction.ANSWER,
        "No change.",
        state_version=state_version,
        observations=[],
    )
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            ),
            decision(AgentBoundaryAction.ANSWER, content="No change."),
        ]
    )
    loop, _ = loop_for(model, capability=capability)

    result = await loop.run(
        AgentLoopInput(
            user_message="refresh",
            state_version=state_version,
            prior_response_fingerprints={prior},
        ),
        context=context(),
    )

    assert result.stop_reason is AgentStopReason.ANSWERED
    assert len(result.observations) == 1


async def test_trace_is_ordered_safe_and_transports_have_semantic_parity() -> None:
    capability = read_capability()
    observation = CapabilityObservation(
        capability_id=capability.id,
        status="succeeded",
        summary="done",
        workflow_ref={"workflow_run_id": 4, "project_id": "alpha"},
        receipt_ref={"receipt_id": 8},
        safe_details={"provider_payload": "must-not-enter-trace", "token": "secret"},
    )
    handler = RecordingHandler(capability, observation=observation)
    model = ScriptedModel(
        [
            decision(
                AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=capability.id,
                arguments={"project_id": "alpha"},
            ),
            decision(AgentBoundaryAction.ANSWER, content="Done."),
        ]
    )
    loop, _ = loop_for(model, capability=capability, handler=handler)
    result = await loop.run(AgentLoopInput(user_message="read"), context=context())

    non_stream = non_stream_transport(result)
    stream = [event async for event in stream_transport(result)]
    trace_payloads = [json.loads(data) for kind, data in stream if kind == "agent_trace"]
    observation_payloads = [
        json.loads(data) for kind, data in stream if kind == "agent_observation"
    ]
    final_payload = json.loads(stream[-1][1])

    assert trace_payloads == non_stream["trace"]
    assert observation_payloads == non_stream["observations"]
    assert "output" not in final_payload
    assert "typed_response" not in final_payload
    assert final_payload["stop_reason"] == non_stream["stop_reason"]
    assert [item["sequence"] for item in trace_payloads] == [1, 2]
    assert [kind for kind, _ in stream] == [
        "agent_trace",
        "agent_observation",
        "agent_trace",
        "agent_response",
        "agent_final",
    ]
    typed_payload = json.loads(next(data for kind, data in stream if kind == "agent_response"))
    assert typed_payload == non_stream["typed_response"]
    trace_json = json.dumps(trace_payloads)
    assert "must-not-enter-trace" not in trace_json
    assert "secret" not in trace_json
    assert "prompt" not in trace_json


async def test_card_hydration_replaces_stale_workflow_fact_before_exact_persistence() -> None:
    fact = ResponseFact(
        fact_id="workflow:44:status",
        kind=ResponseFactKind.WORKFLOW_STATUS,
        label="Workflow for alpha",
        value="running",
        mandatory=True,
        workflow_run_id=44,
    )
    typed = render_typed_response(
        action="answer",
        content=None,
        plan=ResponseSynthesisPlan(fact_ids=[]),
        facts=[fact],
        state_version="authority",
        stop_reason="answered",
    )
    assert typed.synthesis_source == "deterministic_fallback"
    assert "running" in typed.content.lower()
    result = AgentLoopResult(
        action="answer",
        output=typed.content,
        stop_reason="answered",
        state_version="authority",
        response_fingerprint=typed.factual_fingerprint,
        typed_response=typed,
    )
    terminal = WorkflowCard(
        workflow_run_id=44,
        project_id="alpha",
        capability_id="mcp.docs.generate_project_documentation",
        label="Documentation generation",
        status="failed",
        display_label="Failed",
        display_tone="danger",
        is_active=False,
        error_message="The exact child failed.",
        started_at="2026-07-19T01:00:00Z",
        finished_at="2026-07-19T01:00:12Z",
        projected_at="2026-07-19T01:00:13Z",
        elapsed_ms=12_000,
        timing_complete=True,
    )

    class Projector:
        async def project_many(self, ids, **kwargs):  # noqa: ANN001
            assert ids == [44]
            assert kwargs["owner_user_id"] == 7
            assert kwargs["conversation_id"] == 13
            assert kwargs["reconcile"] is True
            return [terminal]

    hydrated = await hydrate_authoritative_workflow_cards(
        result,
        db=object(),
        user_id=7,
        conversation_id=13,
        project_scope=["alpha"],
        access_token="server-token",
        projector=Projector(),
    )
    message = SimpleNamespace(content="", structured_answer=None)
    persisted = persist_typed_agent_response(message, hydrated)
    non_stream = non_stream_transport(hydrated)
    stream = [event async for event in stream_transport(hydrated)]
    streamed = json.loads(next(data for kind, data in stream if kind == "agent_response"))

    assert "Failed" in hydrated.output
    assert "exact child failed" in hydrated.output
    assert "running" not in hydrated.output.lower()
    assert hydrated.typed_response is not None
    assert hydrated.typed_response.workflow_cards == [terminal]
    assert all(fact.value != "running" for fact in hydrated.typed_response.facts)
    assert persisted == non_stream["typed_response"] == streamed
    assert message.structured_answer == persisted
    assert message.content == hydrated.output

    repeated = enforce_canonical_repeat_guard(hydrated, {hydrated.response_fingerprint})
    assert repeated.stop_reason is AgentStopReason.REPEATED_RESPONSE
    assert repeated.typed_response is not None
    assert repeated.typed_response.kind == "no_progress"
    assert repeated.response_fingerprint == hydrated.response_fingerprint
    assert "running" not in repeated.output.lower()


async def test_context_builder_reads_parent_and_exact_children_not_activity_history(
    monkeypatch,
) -> None:
    child = SimpleNamespace(
        id=3,
        version=2,
        provider_id="mcp.docs",
        external_run_id="91",
        project_id="alpha",
        status="failed",
        provider_status="orphaned at restart",
        provider_timestamp=None,
        last_observed_at=SimpleNamespace(isoformat=lambda: "2026-07-18T20:01:00Z"),
        error_message="orphaned Authorization: Bearer child-secret-token",
        cancellation_state="none",
    )
    run = SimpleNamespace(
        id=4,
        version=5,
        project_id="alpha",
        capability_id="mcp.docs.generate_project_documentation",
        status=SimpleNamespace(value="failed"),
        output_summary="provider said api_key=parent-secret-key",
        error_message="failure token=parent-token-value",
        started_at=SimpleNamespace(isoformat=lambda: "2026-07-18T20:00:00Z"),
        finished_at=SimpleNamespace(isoformat=lambda: "2026-07-18T20:01:00Z"),
        children=[child],
    )

    async def list_runs(self, scope, **kwargs):  # noqa: ANN001
        del self
        assert scope == ["alpha"]
        assert kwargs["conversation_id"] == 13
        assert kwargs["owner_user_id"] == 7
        return [run]

    monkeypatch.setattr(
        "src.harness.agent_loop_context.HarnessWorkflowRepository.list_runs",
        list_runs,
    )
    conversation = SimpleNamespace(
        id=13,
        messages=[SimpleNamespace(id=1, role="user", content="status?")],
    )

    turn = await build_authoritative_agent_input(
        conversation=conversation,
        query="what happened?",
        db=object(),
        user_id=7,
        project_scope=["alpha"],
        conversation_state=ConversationState(
            id=1,
            conversation_id=13,
            owner_user_id=7,
            version=1,
        ),
    )

    assert turn.workflow_state[0]["status"] == "failed"
    assert turn.workflow_state[0]["children"][0]["external_run_id"] == "91"
    assert "runtime_metadata" not in json.dumps(turn.workflow_state)
    assert "activity" not in json.dumps(turn.workflow_state)
    projected = json.dumps(turn.workflow_state)
    assert "parent-secret" not in projected
    assert "child-secret" not in projected
    assert projected.count("[redacted]") == 3

    model = ScriptedModel(
        [decision(AgentBoundaryAction.ANSWER, content="The workflow failed safely.")]
    )
    loop, _ = loop_for(model)
    result = await loop.run(turn, context=context())
    request_json = model.requests[0].model_dump_json()
    transport_json = json.dumps(non_stream_transport(result))
    assert "parent-secret" not in request_json
    assert "child-secret" not in request_json
    assert "parent-secret" not in transport_json
    assert "child-secret" not in transport_json


async def test_context_builder_seeds_state_neutral_repeat_detection(monkeypatch) -> None:
    async def no_runs(self, scope, **kwargs):  # noqa: ANN001
        del self, scope, kwargs
        return []

    monkeypatch.setattr(
        "src.harness.agent_loop_context.HarnessWorkflowRepository.list_runs",
        no_runs,
    )
    conversation = SimpleNamespace(
        id=13,
        messages=[SimpleNamespace(id=1, role="assistant", content="No change.")],
    )
    turn = await build_authoritative_agent_input(
        conversation=conversation,
        query="again",
        db=object(),
        user_id=7,
        project_scope=["alpha"],
        conversation_state=ConversationState(
            id=1,
            conversation_id=13,
            owner_user_id=7,
            version=1,
        ),
    )
    model = ScriptedModel([decision(AgentBoundaryAction.ANSWER, content="No change.")])
    loop, _ = loop_for(model)

    result = await loop.run(turn, context=context())

    assert result.stop_reason is AgentStopReason.REPEATED_RESPONSE
