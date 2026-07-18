from __future__ import annotations

import json

import pytest

from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.harness.evidence_policy import (
    ALL_EVIDENCE_SOURCES,
    EvidenceSource,
    resolve_capability_turn_semantics,
)
from src.harness.manifest import RuntimeCapabilityManifest, RuntimeCapabilityTool
from src.harness.models import LOG_SEARCH_EVIDENCE_CAPABILITY
from src.harness.planner import (
    EvidencePolicy,
    PlannerAction,
    TurnExecutionMode,
    TurnPlan,
    TurnTaskType,
    plan_harness_turn,
)
from src.harness.registry import base_capability_registry


def test_turn_plan_rejects_overlapping_or_unapproved_evidence_policy() -> None:
    with pytest.raises(ValueError, match="both allowed and denied"):
        EvidencePolicy(allowed=["runtime_evidence"], denied=["runtime_evidence"])
    with pytest.raises(ValueError, match="must be allowed"):
        EvidencePolicy(allowed=["indexed_documentation"], required=["runtime_evidence"])

    plan = TurnPlan(
        objective="List documented jobs.",
        task_type=TurnTaskType.DOCUMENT_ANSWER,
        resolved_scope={"project_id": "alitalend"},
        evidence_policy=EvidencePolicy(
            allowed=["indexed_documentation"],
            denied=["runtime_evidence"],
            preferred=["indexed_documentation"],
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="inventory",
        confidence=0.9,
    )
    assert plan.allows("indexed_documentation")
    assert not plan.allows("runtime_evidence")


def test_enabled_capability_semantics_use_closed_turn_plan_evidence_vocabulary() -> None:
    for capability in base_capability_registry().list(enabled_only=True):
        assert set(capability.evidence_sources).issubset(ALL_EVIDENCE_SOURCES)
        if capability.metadata.get("runtime_hidden") is not True:
            assert resolve_capability_turn_semantics(capability) is not None


def test_runtime_log_search_semantics_resolve_to_runtime_mode() -> None:
    semantics = resolve_capability_turn_semantics(LOG_SEARCH_EVIDENCE_CAPABILITY)

    assert semantics is not None
    assert semantics.mode == "runtime_answer"
    assert semantics.task_type == "runtime_answer"
    assert LOG_SEARCH_EVIDENCE_CAPABILITY.evidence_sources == [
        EvidenceSource.RUNTIME_EVIDENCE
    ]


def _manifest() -> RuntimeCapabilityManifest:
    return RuntimeCapabilityManifest(
        tools=[
            RuntimeCapabilityTool(
                id="docs.generate",
                label="Generate documentation",
                action="generate",
                provider_type="documentation_provider",
                input_schema={
                    "type": "object",
                    "required": ["project_id"],
                    "additionalProperties": False,
                    "properties": {"project_id": {"type": "string", "minLength": 1}},
                },
                output_type="receipt",
                risk_tier="triggers_workflow",
                activity_label="Generate documentation",
            )
        ],
        total=1,
        filtered=0,
    )


async def test_planner_accepts_valid_manifest_tool_call() -> None:
    async def fake(messages, runtime_tools):  # noqa: ANN001
        tool_name = runtime_tools[0].name
        assert tool_name.startswith("nexus_tool_")
        assert "." not in tool_name
        assert "Capability docs.generate" in runtime_tools[0].description
        assert "provider_id" not in json.dumps(messages)
        serialized_messages = json.dumps(messages)
        assert tool_name in serialized_messages
        assert "tool_name" in serialized_messages
        return ToolAwareLLMResponse(
            provider="ignored-provider",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="call-1",
                    tool_name=tool_name,
                    arguments={"project_id": "project-a"},
                )
            ],
        )

    decision = await plan_harness_turn(
        user_message="Generate docs",
        manifest=_manifest(),
        project_options=[{"id": "project-a", "display_name": "Project A", "secret": "no"}],
        pending_workflow_summary=None,
        conversation_history=[],
        planner=fake,
    )

    assert decision.action == PlannerAction.EXECUTE
    assert decision.tool_calls[0].capability_id == "docs.generate"
    assert decision.tool_calls[0].arguments == {"project_id": "project-a"}


async def test_planner_does_not_execute_explicit_intent_capability_for_usage_question() -> None:
    calls = 0
    manifest = _manifest()
    manifest.tools[0].requires_explicit_intent = True
    manifest.tools[0].explicit_intent_terms = ["generate", "start", "run"]

    def fake(messages, runtime_tools):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolAwareLLMResponse(
                provider="test",
                tool_calls=[
                    RuntimeToolCallRequest(
                        call_id="call-1",
                        tool_name=runtime_tools[0].name,
                        arguments={"project_id": "project-a"},
                    )
                ],
            )
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "answer",
                    "tool_calls": [],
                    "confidence": 0.9,
                    "answer": "Name a project and Pack when you want to start a run.",
                    "clarification": None,
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="How can I use those Packs?",
        manifest=manifest,
        planner=fake,
    )

    assert calls == 2
    assert decision.action == PlannerAction.ANSWER
    assert decision.tool_calls == []


async def test_planner_rejects_catalog_for_explicit_generation_and_retries_generation() -> None:
    calls = 0
    manifest = _manifest()
    generation = manifest.tools[0]
    generation.requires_explicit_intent = True
    generation.explicit_intent_terms = ["generate", "start", "run"]
    catalog = RuntimeCapabilityTool(
        id="docs.list_packs",
        label="List documentation Packs",
        action="list_packs",
        provider_type="documentation_provider",
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
        output_type="pack_catalog",
        risk_tier="read_only",
        activity_label="List documentation Packs",
        forbidden_intent_terms=["generate", "start", "run"],
    )
    manifest.tools.append(catalog)
    manifest.total = 2

    def fake(messages, runtime_tools):  # noqa: ANN001
        nonlocal calls
        calls += 1
        selected = runtime_tools[1] if calls == 1 else runtime_tools[0]
        arguments = {} if calls == 1 else {"project_id": "project-a"}
        return ToolAwareLLMResponse(
            provider="test",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id=f"call-{calls}",
                    tool_name=selected.name,
                    arguments=arguments,
                )
            ],
        )

    decision = await plan_harness_turn(
        user_message="Generate documentation for project-a using the Security Pack.",
        manifest=manifest,
        project_options=[{"id": "project-a", "display_name": "Project A"}],
        planner=fake,
    )

    assert calls == 2
    assert decision.action == PlannerAction.EXECUTE
    assert decision.tool_calls[0].capability_id == "docs.generate"


async def test_planner_accepts_validated_text_encoded_tool_call() -> None:
    def fake(messages, runtime_tools):  # noqa: ANN001
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "execute",
                    "tool_calls": [
                        {
                            "tool_name": runtime_tools[0].name,
                            "parameters": {"project_id": "project-a"},
                        }
                    ],
                    "confidence": 0.9,
                    "answer": None,
                    "clarification": None,
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="Generate docs", manifest=_manifest(), planner=fake
    )

    assert decision.action == PlannerAction.EXECUTE
    assert decision.confidence == 0.9
    assert decision.tool_calls[0].capability_id == "docs.generate"


async def test_planner_accepts_fenced_text_tool_call_with_arguments_alias() -> None:
    def fake(messages, runtime_tools):  # noqa: ANN001
        return ToolAwareLLMResponse(
            provider="test",
            text=(
                "```json\n"
                + json.dumps(
                    {
                        "action": "execute",
                        "tool_calls": [
                            {
                                "tool_name": runtime_tools[0].name,
                                "arguments": {"project_id": "project-a"},
                            }
                        ],
                        "confidence": 0.95,
                        "answer": None,
                        "clarification": None,
                    }
                )
                + "\n```"
            ),
        )

    decision = await plan_harness_turn(
        user_message="Generate docs", manifest=_manifest(), planner=fake
    )

    assert decision.action == PlannerAction.EXECUTE
    assert decision.tool_calls[0].arguments == {"project_id": "project-a"}


async def test_planner_normalizes_common_function_shape_and_string_confidence() -> None:
    def fake(messages, runtime_tools):  # noqa: ANN001
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "execute",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": runtime_tools[0].name,
                                "arguments": json.dumps({"project_id": "project-a"}),
                            },
                        }
                    ],
                    "confidence": "high",
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="Generate docs", manifest=_manifest(), planner=fake
    )

    assert decision.action == PlannerAction.EXECUTE
    assert decision.confidence == 0.85
    assert decision.tool_calls[0].arguments == {"project_id": "project-a"}


async def test_planner_rejects_extra_fields_in_text_encoded_tool_call() -> None:
    def fake(messages, runtime_tools):  # noqa: ANN001
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "execute",
                    "tool_calls": [
                        {
                            "tool_name": runtime_tools[0].name,
                            "parameters": {"project_id": "project-a"},
                            "provider_id": "must-not-pass",
                        }
                    ],
                    "confidence": 0.9,
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="Generate docs", manifest=_manifest(), planner=fake
    )

    assert decision.action == PlannerAction.ANSWER
    assert decision.confidence == 0.0


@pytest.mark.parametrize(
    "tool_calls",
    [
        [RuntimeToolCallRequest("1", "unknown", {})],
        [RuntimeToolCallRequest("1", "docs.generate", {})],
        [RuntimeToolCallRequest("1", "docs.generate", {"project_id": "a", "secret": "x"})],
        [
            RuntimeToolCallRequest(str(i), "docs.generate", {"project_id": "a"})
            for i in range(4)
        ],
    ],
)
async def test_planner_rejects_untrusted_tool_calls(tool_calls) -> None:  # noqa: ANN001
    async def fake(messages, runtime_tools):  # noqa: ANN001
        return ToolAwareLLMResponse(provider="test", tool_calls=tool_calls)

    decision = await plan_harness_turn(
        user_message="do it", manifest=_manifest(), planner=fake, max_tool_calls=3
    )

    assert decision.action == PlannerAction.ANSWER
    assert decision.tool_calls == []
    assert decision.confidence == 0.0


@pytest.mark.parametrize(
    "response",
    [
        ToolAwareLLMResponse(provider="test", no_tool_support=True),
        ToolAwareLLMResponse(provider="test", text="not json"),
        ToolAwareLLMResponse(provider="test"),
    ],
)
async def test_planner_has_safe_no_tool_and_invalid_fallback(response) -> None:  # noqa: ANN001
    decision = await plan_harness_turn(
        user_message="hello", manifest=_manifest(), planner=lambda *_: response
    )

    assert decision.action == PlannerAction.ANSWER
    assert decision.tool_calls == []


async def test_planner_accepts_strict_non_tool_decision_and_limits_history() -> None:
    captured = {}

    def fake(messages, runtime_tools):  # noqa: ANN001
        captured["messages"] = messages
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "clarify",
                    "tool_calls": [],
                    "confidence": 0.7,
                    "answer": None,
                    "clarification": "Which project should I use?",
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="Generate docs",
        manifest=_manifest(),
        conversation_history=[{"role": "user", "content": str(i)} for i in range(10)],
        history_limit=2,
        planner=fake,
    )

    assert decision.action == PlannerAction.CLARIFY
    assert decision.clarification == "Which project should I use?"
    assert [message["content"] for message in captured["messages"][-3:]] == [
        "8",
        "9",
        "Generate docs",
    ]


async def test_planner_exposes_bounded_continuation_and_accepts_replace() -> None:
    captured: dict[str, object] = {}

    def fake(messages, _runtime_tools):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "replace",
                    "tool_calls": [],
                    "confidence": 0.95,
                    "answer": None,
                    "clarification": None,
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="I'm not asking about logs",
        manifest=_manifest(),
        active_continuation={
            "continuation_id": "runtime-1",
            "version": 2,
            "objective": "Search runtime logs",
            "task_type": "runtime_answer",
            "missing_slots": ["time_window"],
            "slots": [{"name": "time_window", "required": True, "options": []}],
            "secret": "must-not-leak",
        },
        planner=fake,
    )

    assert decision.action is PlannerAction.REPLACE
    system = str(captured["system"])
    assert "active_continuation" in system
    assert "time_window" in system
    assert "must-not-leak" not in system


async def test_planner_drops_empty_history_messages_rejected_by_providers() -> None:
    def fake(messages, runtime_tools):  # noqa: ANN001
        assert all(message["content"].strip() for message in messages)
        return ToolAwareLLMResponse(
            provider="test",
            text=json.dumps(
                {
                    "action": "answer",
                    "tool_calls": [],
                    "confidence": 0.8,
                    "answer": "Hello",
                    "clarification": None,
                }
            ),
        )

    decision = await plan_harness_turn(
        user_message="hello",
        manifest=_manifest(),
        conversation_history=[
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "   "},
        ],
        planner=fake,
    )

    assert decision.action == PlannerAction.ANSWER


async def test_unavailable_manifest_tool_is_not_offered_or_accepted() -> None:
    manifest = _manifest()
    manifest.tools[0].available = False

    def fake(messages, runtime_tools):  # noqa: ANN001
        assert runtime_tools == []
        return ToolAwareLLMResponse(
            provider="test",
            tool_calls=[RuntimeToolCallRequest("1", "docs.generate", {"project_id": "a"})],
        )

    decision = await plan_harness_turn(user_message="do it", manifest=manifest, planner=fake)

    assert decision.action == PlannerAction.ANSWER
    assert decision.tool_calls == []
