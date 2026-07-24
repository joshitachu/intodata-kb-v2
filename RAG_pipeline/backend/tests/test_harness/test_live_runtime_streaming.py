from __future__ import annotations

import json
import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import build_readonly_primary_runtime, run_readonly_primary_turn
from src.harness.registry import default_capability_registry
from src.harness.agent_loop import AgentStopReason
from src.harness.models import EvidenceQueryResult, METADATA_QUERY_CAPABILITY
from src.harness.run_budgets import LayeredBudgetController, LayeredBudgetLimits

pytestmark = pytest.mark.asyncio


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


class SlowModel(ScriptedModel):
    async def _agenerate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        await asyncio.sleep(0.05)
        return await super()._agenerate(*args, **kwargs)


def _tight_budget(**updates) -> LayeredBudgetController:
    values = {
        "model_timeout_seconds": 0.01,
        "read_tool_timeout_seconds": 0.01,
        "write_tool_timeout_seconds": 0.01,
        "workflow_handoff_timeout_seconds": 0.01,
        "soft_deadline_seconds": 1,
        "hard_deadline_seconds": 2,
        "max_model_calls": 4,
        "synthesis_reserve_calls": 1,
        "max_tool_calls": 2,
        "max_cost_usd": 1,
    }
    values.update(updates)
    return LayeredBudgetController(LayeredBudgetLimits(**values))


async def test_answer_deltas_are_emitted_only_after_live_runtime_progress() -> None:
    grounding = json.dumps(
        {"answer_status": "grounded", "citations": [], "disclosures": []}
    )
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[AIMessage(content=f"Safe direct answer.\n```nexus-grounding\n{grounding}\n```")]
        ),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
    )
    events: list[tuple[str, dict]] = []

    async def capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Hello",
        event_callback=capture,
    )
    kinds = [kind for kind, _ in events]
    assert "runtime.update" in kinds
    assert "assistant.delta" in kinds
    assert kinds.index("runtime.update") < kinds.index("assistant.delta")
    public_text = "".join(
        payload["content"] for kind, payload in events if kind == "assistant.delta"
    )
    assert public_text == turn.grounded.content == "Safe direct answer."
    assert "nexus-grounding" not in public_text


async def test_model_timeout_has_its_own_safe_stop_reason() -> None:
    registry = default_capability_registry()
    runtime = build_readonly_primary_runtime(
        model=SlowModel(responses=[AIMessage(content="late")]),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
        budget_controller=_tight_budget(),
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Hello",
    )
    assert turn.result.stop_reason is AgentStopReason.MODEL_TIMEOUT
    assert "model took too long" in turn.grounded.content
    assert turn.consequential_executions == 0


async def test_tool_timeout_is_not_reported_as_no_evidence() -> None:
    calls = 0

    async def slow_handler(arguments, context):  # noqa: ANN001
        nonlocal calls
        del arguments, context
        calls += 1
        await asyncio.sleep(0.05)
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={METADATA_QUERY_CAPABILITY.id: slow_handler},
    )
    model = ScriptedModel(responses=[AIMessage(
        content="",
        tool_calls=[{
            "name": "query_project_metadata",
            "args": {"query_type": "project_list"},
            "id": "slow-tool",
            "type": "tool_call",
        }],
    )])
    runtime = build_readonly_primary_runtime(
        model=model,
        registry=registry,
        executor=executor,
        context=CapabilityExecutionContext(user_id=1, conversation_id=1),
        scope=ManifestScope(include_bounded_loop_only=True),
        budget_controller=_tight_budget(model_timeout_seconds=1),
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="List projects",
    )
    assert calls == 1
    assert turn.result.stop_reason is AgentStopReason.TOOL_TIMEOUT
    assert "tool took too long" in turn.grounded.content
