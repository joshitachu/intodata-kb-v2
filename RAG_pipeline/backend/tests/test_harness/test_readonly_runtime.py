from __future__ import annotations


import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.grounding import AnswerStatus, GroundingVerdict
from src.harness.manifest import ManifestScope
from src.harness.models import (
    METADATA_QUERY_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.readonly_runtime import (
    _SAFE_FALLBACK_TEXT,
    build_readonly_primary_runtime,
    readonly_manifest_partition,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import GovernanceClass, build_semantic_tool_snapshot

pytestmark = pytest.mark.asyncio


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=1,
        conversation_id=1,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _scope() -> ManifestScope:
    return ManifestScope(include_bounded_loop_only=True, permissions={"manage_projects"})


def _metadata_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "query_project_metadata",
                "args": {"query_type": "project_list"},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _grounding_block(status: str, refs: list[str], disclosures: list[str]) -> str:
    import json

    citations = [{"claim_key": "fact", "evidence_refs": refs}] if refs else []
    body = json.dumps(
        {"answer_status": status, "citations": citations, "disclosures": disclosures}
    )
    return f"```nexus-grounding\n{body}\n```"


def _make_executor(calls: list[str]):
    async def metadata_handler(arguments, context):  # noqa: ANN001
        calls.append("metadata")
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="ev-1",
                    capability_id=METADATA_QUERY_CAPABILITY.id,
                    source_type="metadata",
                    title="Projects",
                    summary="Two authorized projects exist.",
                )
            ],
        )

    registry = default_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={METADATA_QUERY_CAPABILITY.id: metadata_handler},
    )
    return registry, executor


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
async def test_consequential_capabilities_filtered_from_model_tool_set() -> None:
    registry = default_capability_registry()
    snapshot = build_semantic_tool_snapshot(registry, scope=_scope())
    reads, consequential = readonly_manifest_partition(snapshot)
    read_names = {record.semantic_name for record in reads}
    assert "query_project_metadata" in read_names
    # No consequential semantic tool is model-facing.
    assert "create_pack_draft" not in read_names
    assert all(
        record.governance_class == GovernanceClass.READ for record in reads
    )
    assert consequential  # consequential capabilities exist but are filtered out


async def test_direct_answer_uses_no_tool() -> None:
    registry, executor = _make_executor([])
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                AIMessage(
                    content="Hello, I can help with your Nexus projects.\n"
                    + _grounding_block("grounded", [], [])
                )
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="hello",
    )
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.executor_read_calls == 0
    assert turn.used_fallback is False
    assert "help" in turn.grounded.content


async def test_sequential_reads_then_one_grounded_answer() -> None:
    calls: list[str] = []
    registry, executor = _make_executor(calls)
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                _metadata_call("read-a"),
                AIMessage(
                    content="You have two authorized projects.\n"
                    + _grounding_block("grounded", ["ev-1"], [])
                ),
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Which projects exist?",
    )
    # A non-shadow-safe READ executed for real through the governed executor.
    assert calls == ["metadata"]
    assert turn.executor_read_calls == 1
    assert turn.consequential_executions == 0
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.grounded.answer_status is AnswerStatus.GROUNDED
    assert "ev-1" in turn.grounded.cited_refs
    assert turn.result.typed_response is None
    assert turn.result.action is not None


async def test_bounded_correction_then_valid_rewrite() -> None:
    calls: list[str] = []
    registry, executor = _make_executor(calls)
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                _metadata_call("read-a"),
                # First answer: grounded but cites nothing though evidence exists.
                AIMessage(
                    content="You have two projects.\n"
                    + _grounding_block("grounded", [], [])
                ),
                # Rewrite after correction: cites the allowed evidence.
                AIMessage(
                    content="You have two authorized projects.\n"
                    + _grounding_block("grounded", ["ev-1"], [])
                ),
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
        interrupt_before_tools=False,
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Which projects exist?",
        max_corrections=1,
    )
    assert turn.correction_rounds == 1
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.used_fallback is False
    assert "ev-1" in turn.grounded.cited_refs


async def test_budget_exhaustion_emits_concise_safe_fallback() -> None:
    calls: list[str] = []
    registry, executor = _make_executor(calls)
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                _metadata_call("read-a"),
                AIMessage(
                    content="You have projects.\n" + _grounding_block("grounded", [], [])
                ),
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Which projects exist?",
        max_corrections=0,
    )
    assert turn.used_fallback is True
    assert turn.grounded.content == _SAFE_FALLBACK_TEXT
    # The fallback leaks no internal identifiers or raw payloads.
    assert "rag." not in turn.grounded.content
    assert "{" not in turn.grounded.content


async def test_fallback_trips_repeat_guard_when_duplicating_prior() -> None:
    calls: list[str] = []
    registry, executor = _make_executor(calls)
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[AIMessage(content="")],  # forces empty -> fallback
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=_scope(),
    )
    from src.harness.agent_loop import AgentBoundaryAction, response_fingerprint

    prior = {
        response_fingerprint(
            AgentBoundaryAction.ANSWER,
            _SAFE_FALLBACK_TEXT,
            state_version="v0",
            observations=[],
        )
    }
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Which projects exist?",
        state_version="v0",
        prior_response_fingerprints=prior,
        max_corrections=0,
    )
    assert turn.used_fallback is True
    assert turn.result.stop_reason.value == "repeated_response"
