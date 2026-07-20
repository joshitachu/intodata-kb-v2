from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    EvidenceQueryResult,
)
from src.harness.registry import CapabilityRegistry, default_capability_registry
from src.harness.semantic_shadow_runtime import build_semantic_shadow_runtime
from src.harness.semantic_tools import (
    SemanticEventLedger,
    SemanticToolGateway,
    build_semantic_structured_tools,
    build_semantic_tool_snapshot,
)


class ScriptedModel(FakeMessagesListChatModel):
    bound_tools: list[Any] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        self.bound_tools = list(tools)
        assert tool_choice == "auto"
        return self


def _call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=3,
        conversation_id=9,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _scope() -> ManifestScope:
    return ManifestScope(
        include_bounded_loop_only=True,
        permissions={"manage_projects"},
    )


def _payloads(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(str(message.content))
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]


async def test_direct_assistant_termination_uses_no_response_tool() -> None:
    model = ScriptedModel(responses=[AIMessage(content="Nexus answered directly.")])
    runtime = build_semantic_shadow_runtime(
        model=model,
        registry=default_capability_registry(),
        executor=HarnessCapabilityExecutor(default_capability_registry()),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Who are you?"}]}
    )

    assert result["messages"][-1].content == "Nexus answered directly."
    assert not _payloads(result)
    assert all(tool.name != "nexus_agent_respond" for tool in model.bound_tools)


async def test_sequential_read_observation_read_observation_answer() -> None:
    calls: list[dict[str, Any]] = []

    async def adapter(arguments, context):  # noqa: ANN001
        calls.append(dict(arguments))
        return EvidenceQueryResult(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            status="succeeded",
        )

    registry = default_capability_registry()
    runtime = build_semantic_shadow_runtime(
        model=ScriptedModel(
            responses=[
                _call("read_conversation_summary", {"max_messages": 1}, "call-a"),
                _call("read_conversation_summary", {"max_messages": 2}, "call-b"),
                AIMessage(content="Both bounded observations were consumed."),
            ]
        ),
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            evidence_handlers={CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: adapter},
        ),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Read two bounded windows."}]}
    )

    assert calls == [{"max_messages": 1}, {"max_messages": 2}]
    assert [item["status"] for item in _payloads(result)] == ["succeeded", "succeeded"]
    assert result["messages"][-1].content == "Both bounded observations were consumed."
    assert [event.sequence for event in runtime.ledger.events] == list(
        range(1, len(runtime.ledger.events) + 1)
    )


async def test_validation_failure_then_corrected_arguments_succeeds() -> None:
    calls = 0

    async def adapter(arguments, context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return EvidenceQueryResult(
            capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
            status="succeeded",
        )

    registry = default_capability_registry()
    runtime = build_semantic_shadow_runtime(
        model=ScriptedModel(
            responses=[
                _call("read_conversation_summary", {"max_messages": 99}, "call-invalid"),
                _call("read_conversation_summary", {"max_messages": 2}, "call-corrected"),
                AIMessage(content="The corrected read succeeded."),
            ]
        ),
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            evidence_handlers={CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: adapter},
        ),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Retry a bounded read."}]}
    )

    assert calls == 1
    assert [item["status"] for item in _payloads(result)] == [
        "validation_failed",
        "succeeded",
    ]


async def test_unknown_tool_returns_correlated_typed_message() -> None:
    registry = default_capability_registry()
    runtime = build_semantic_shadow_runtime(
        model=ScriptedModel(
            responses=[
                _call("invented_nexus_tool", {}, "call-unknown"),
                AIMessage(content="The unknown tool was not executed."),
            ]
        ),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Hallucinate a tool."}]}
    )

    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[0].tool_call_id == "call-unknown"
    assert json.loads(str(tool_messages[0].content))["status"] == "unknown_tool"


async def test_known_disabled_tool_returns_unavailable_without_handler() -> None:
    base = default_capability_registry()
    disabled = CONVERSATION_SUMMARY_FACTS_CAPABILITY.model_copy(
        update={"enabled": False}
    )
    search = base.get("rag.docs.search_evidence")
    registry = CapabilityRegistry(
        [disabled, search],
        handlers=[
            base.handler_for(CONVERSATION_SUMMARY_FACTS_CAPABILITY.id),
            base.handler_for(search.id),
        ],
    )
    runtime = build_semantic_shadow_runtime(
        model=ScriptedModel(
            responses=[
                _call("read_conversation_summary", {}, "call-unavailable"),
                AIMessage(content="The disabled capability was unavailable."),
            ]
        ),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=_context(),
        scope=_scope(),
    )
    result = await runtime.graph.ainvoke(
        {"messages": [{"role": "user", "content": "Use the disabled tool."}]}
    )

    assert _payloads(result)[0]["status"] == "unavailable"


async def test_gateway_refuses_execution_without_middleware_context() -> None:
    class NeverExecutor:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("middleware-free call reached executor")

    snapshot = build_semantic_tool_snapshot(
        default_capability_registry(), scope=_scope()
    )
    gateway = SemanticToolGateway(
        snapshot=snapshot,
        executor=NeverExecutor(),
        context=_context(),
        ledger=SemanticEventLedger(),
    )
    envelope = await gateway.execute(
        "read_conversation_summary", {"max_messages": 1}
    )

    assert envelope.status == "denied"
    assert envelope.error == {
        "code": "middleware_proposal_missing",
        "recovery": "Respect the authority boundary and offer a permitted alternative.",
    }


def test_structured_tool_schema_is_exact_executor_schema_copy() -> None:
    registry = default_capability_registry()
    snapshot = build_semantic_tool_snapshot(registry, scope=_scope())
    gateway = SemanticToolGateway(
        snapshot=snapshot,
        executor=HarnessCapabilityExecutor(registry),
        context=_context(),
    )
    tools = build_semantic_structured_tools(snapshot, gateway)

    for tool in tools:
        canonical_id = snapshot.semantic_to_canonical[tool.name]
        assert tool.args_schema == registry.get(canonical_id).input_schema
        assert tool.args_schema is not registry.get(canonical_id).input_schema
