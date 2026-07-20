from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    PACK_DRAFT_CREATE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.registry import CapabilityRegistry, default_capability_registry
from src.harness.semantic_shadow_runtime import build_semantic_shadow_runtime
from src.harness.semantic_tools import (
    MAX_TOOL_MESSAGE_BYTES,
    SemanticSnapshotError,
    SemanticToolGateway,
    build_semantic_tool_snapshot,
    project_capability_observation,
    serialize_tool_envelope,
)


class ScriptedSemanticModel(FakeMessagesListChatModel):
    bound_schemas: list[dict[str, Any]] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        assert tool_choice == "auto"
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
        permissions={"manage_projects"},
    )


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=7,
        conversation_id=11,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _tool_payloads(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(str(message.content))
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]


def test_semantic_snapshot_is_complete_stable_and_schema_identical() -> None:
    registry = default_capability_registry()
    first = build_semantic_tool_snapshot(registry, scope=_scope())
    second = build_semantic_tool_snapshot(registry, scope=_scope())

    assert len(first.tools) == 21
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
            CapabilityRegistry(
                [read, collision], handlers=[handler_read, collision_handler]
            ),
            scope=_scope(),
        )

    with pytest.raises(SemanticSnapshotError, match="drift"):
        build_semantic_tool_snapshot(
            CapabilityRegistry([read], handlers=[handler_read]),
            scope=_scope(),
            expected_names={read.id: "different_name"},
        )

    open_schema = read.model_copy(
        update={"input_schema": {"type": "object", "properties": {}}}
    )
    with pytest.raises(SemanticSnapshotError, match="open object"):
        build_semantic_tool_snapshot(
            CapabilityRegistry(
                [open_schema], handlers=[_matching_handler(open_schema, handler_read)]
            ),
            scope=_scope(),
        )


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
    assert observation.status == "denied"
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
            capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
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
                _call("create_pack_draft", {}, "call-write-proposal"),
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
                    ("create_pack_draft", {}, "call-create"),
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


def test_gateway_has_no_model_supplied_authority_surface() -> None:
    fields = set(SemanticToolGateway.execute.__annotations__)
    assert fields == {"semantic_name", "arguments", "return"}
