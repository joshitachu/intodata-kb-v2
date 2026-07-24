from __future__ import annotations


from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

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
    READ_ONLY_PRIMARY_SYSTEM_PROMPT,
    _SAFE_FALLBACK_TEXT,
    _humanize_visible_content,
    _parse_grounded_answer,
    build_readonly_primary_chat_model,
    build_readonly_primary_runtime,
    readonly_manifest_partition,
    run_readonly_primary_turn,
)
from src.harness.registry import default_capability_registry
from src.harness.semantic_tools import (
    GovernanceClass,
    ToolEnvelope,
    build_semantic_tool_snapshot,
    serialize_tool_envelope,
)
from src.harness.trusted_answer import TrustedAnswerSink, TrustedRagAnswer
from src.harness import grounding as grounding_module
from src.schemas import DocumentSource

pytestmark = pytest.mark.asyncio


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self


class StaticGraph:
    def __init__(self, messages):  # noqa: ANN001
        self.messages = messages

    async def ainvoke(self, _input):  # noqa: ANN001
        return {"messages": self.messages}


class TrustedAnswerStreamingGraph:
    def __init__(self, sink: TrustedAnswerSink):
        self.sink = sink
        self.post_tool_model_requested = False

    async def astream(self, _input, *, stream_mode):  # noqa: ANN001
        del stream_mode
        tool_call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "answer_from_documentation",
                    "args": {"query": "What does this project do?"},
                    "id": "rag-answer",
                    "type": "tool_call",
                }
            ],
        )
        yield "updates", {"model": {"messages": [tool_call]}}
        self.sink.answer = TrustedRagAnswer(
            content="The complete RAG pipeline answered this directly.",
            document_sources=[
                DocumentSource(
                    document_id=7,
                    filename="01_PROJECT_OVERVIEW.md",
                    score=0.9,
                    text="Project overview evidence.",
                    reference_number=1,
                    chunk_indexes=[0],
                )
            ],
        )
        yield "messages", (
            ToolMessage(
                name="answer_from_documentation",
                content=serialize_tool_envelope(
                    ToolEnvelope(
                        status="succeeded",
                        summary="Retrieved one grounded answer.",
                        evidence_refs=[
                            {
                                "evidence_id": "doc:7:0",
                                "document_id": 7,
                                "source_type": "rag_document",
                            }
                        ],
                    )
                ),
                tool_call_id="rag-answer",
            ),
            {},
        )
        self.post_tool_model_requested = True
        yield "updates", {
            "model": {
                "messages": [
                    AIMessage(content="This redundant synthesis must never run.")
                ]
            }
        }


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        user_id=1,
        conversation_id=1,
        project_scope=["fixture-project"],
        permissions={"manage_projects"},
    )


def _scope() -> ManifestScope:
    return ManifestScope(include_bounded_loop_only=True, permissions={"manage_projects"})


async def test_provider_connection_retry_is_bounded_and_configured() -> None:
    model = build_readonly_primary_chat_model(
        SimpleNamespace(
            rag_default_model="kimi/kimi-k2.5",
            kimi_api_key="placeholder-not-a-credential",
            kimi_llm_model="kimi-k2.5",
            kimi_temperature=0.6,
            kimi_timeout=300.0,
            agent_runtime_v2_provider_max_retries=1,
        )
    )

    assert model.max_retries == 1


async def test_document_selection_prompt_uses_only_indexed_rag_for_document_content() -> None:
    assert "Use documentation search for every request" in (
        READ_ONLY_PRIMARY_SYSTEM_PROMPT
    )
    assert "direct file reads for indexed RAG" in READ_ONLY_PRIMARY_SYSTEM_PROMPT
    assert "generated-document inventory only" not in READ_ONLY_PRIMARY_SYSTEM_PROMPT


async def test_exhaustive_document_questions_stay_on_indexed_rag() -> None:
    assert "Use documentation search for exhaustive inventories" in (
        READ_ONLY_PRIMARY_SYSTEM_PROMPT
    )
    assert "Do not bypass indexed RAG" in READ_ONLY_PRIMARY_SYSTEM_PROMPT
    assert "select the exact inventory Markdown file" not in (
        READ_ONLY_PRIMARY_SYSTEM_PROMPT
    )


async def test_existing_project_orientation_is_not_source_onboarding() -> None:
    registry = default_capability_registry()
    snapshot = build_semantic_tool_snapshot(registry, scope=_scope())
    onboarding = snapshot.tool_for_name("onboard_project")
    documentation = snapshot.tool_for_name("answer_from_documentation")

    assert onboarding is not None
    assert documentation is not None
    assert "existing project" in onboarding.description
    assert "documentation question" in onboarding.description
    assert "DEFAULT choice" in documentation.description


async def test_visible_machine_references_are_rendered_as_human_source_labels() -> None:
    envelope = ToolEnvelope(
        status="succeeded",
        summary="Retrieved evidence.",
        data={
            "items": [
                {
                    "id": "doc:392:3",
                    "title": "02_COMPONENT_INVENTORY.md",
                    "summary": "Ten jobs.",
                }
            ]
        },
        evidence_refs=[{"evidence_id": "doc:392:3"}],
    )

    visible = _humanize_visible_content(
        "The search_documentation result (doc:392:3) agrees with "
        "metadata:project_detail:alitalend.",
        [envelope],
    )

    assert visible == (
        "The indexed documentation result (02_COMPONENT_INVENTORY.md) "
        "agrees with project metadata."
    )


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
    assert "create_pack" not in read_names
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


async def test_grounding_parser_rejects_string_disclosure_shape() -> None:
    answer = _parse_grounded_answer(
        "Hello.\n```nexus-grounding\n"
        '{"answer_status":"grounded","citations":[],"disclosures":"internal note"}'
        "\n```",
        requirement=type("Requirement", (), {"status": None})(),
    )
    assert answer.disclosures == []


async def test_grounding_parser_strips_json_fence_with_inner_marker() -> None:
    answer = _parse_grounded_answer(
        "Hi there!\n\n```json\nnexus-grounding\n"
        '{"answer_status":"complete","citations":[],"disclosures":'
        '"Greeting provided without requiring evidence lookup.",'
        '"plan_required":false}\n```',
        requirement=type("Requirement", (), {"status": None})(),
    )
    assert answer.content == "Hi there!"
    assert answer.answer_status is AnswerStatus.GROUNDED
    assert answer.disclosures == []


async def test_neutral_plan_projection_does_not_force_limitation_rewrite() -> None:
    projection = ToolEnvelope(
        status="succeeded",
        summary="Objective topology projected without persistence.",
        data={"plan_required": False},
        evidence_state_relevant=False,
    )
    graph = StaticGraph(
        [
            ToolMessage(
                content=serialize_tool_envelope(projection),
                tool_call_id="project-objective",
            ),
            AIMessage(
                content="Hello! How can I help?\n"
                + _grounding_block("grounded", [], [])
            ),
        ]
    )
    turn = await run_readonly_primary_turn(
        graph=graph,
        ledger=type("Ledger", (), {"events": []})(),
        snapshot=type("Snapshot", (), {})(),
        user_message="hello there",
    )
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.correction_rounds == 0
    assert turn.grounded.content == "Hello! How can I help?"


async def test_internal_direct_stage_builds_standard_graph_with_zero_tools() -> None:
    registry, executor = _make_executor([])
    runtime = build_readonly_primary_runtime(
        model=ScriptedModel(
            responses=[
                AIMessage(
                    content="Direct answer.\n"
                    + _grounding_block("direct_guidance", [], [])
                )
            ]
        ),
        registry=registry,
        executor=executor,
        context=_context(),
        scope=ManifestScope(
            include_bounded_loop_only=True,
            permissions={"manage_projects"},
            allowed_capability_ids=set(),
        ),
    )
    assert runtime.read_only_tools == ()
    assert runtime.snapshot.tools == ()
    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="hello",
    )
    assert turn.executor_read_calls == 0
    assert turn.consequential_executions == 0


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


async def test_citation_failure_shows_written_answer_without_citations() -> None:
    """A missing/invalid self-reported citation no longer discards the answer.

    Only genuinely unsafe output (leaked internal ids/tool names/raw
    payloads) or a truly empty answer still falls back to the generic safe
    message; a real answer that merely failed the citation contract is shown
    as written, with its (unvalidated) citations/disclosures dropped.
    """
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
    assert turn.grounded.content == "You have projects."
    assert turn.grounded.citations == []
    assert turn.grounded.disclosures == []
    # Still no internal identifiers or raw payloads reach the visible answer.
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


async def test_trusted_answer_sink_short_circuits_to_valid_grounded_answer(
    monkeypatch,
) -> None:
    """A populated TrustedAnswerSink bypasses self-reported grounding entirely."""

    def _validate_must_not_run(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError(
            "GroundingValidator.validate must not run on the trusted-answer path"
        )

    monkeypatch.setattr(
        grounding_module.GroundingValidator, "validate", _validate_must_not_run
    )

    sink = TrustedAnswerSink(
        answer=TrustedRagAnswer(
            content="The deterministic RAG pipeline answered this directly.",
            document_sources=[
                DocumentSource(
                    document_id=7,
                    filename="doc.md",
                    score=0.9,
                    text="Some supporting evidence text.",
                    reference_number=1,
                    chunk_indexes=[0],
                )
            ],
        )
    )
    # The model's own output is irrelevant on this path: no grounding block is
    # parsed and the correction loop never runs.
    graph = StaticGraph([AIMessage(content="ignored model chatter")])
    turn = await run_readonly_primary_turn(
        graph=graph,
        ledger=type("Ledger", (), {"events": []})(),
        snapshot=type("Snapshot", (), {})(),
        user_message="What does the deterministic pipeline say?",
        trusted_answer_sink=sink,
    )
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.used_fallback is False
    assert turn.correction_rounds == 0
    assert turn.grounded.content == (
        "The deterministic RAG pipeline answered this directly."
    )
    assert turn.grounded.answer_status is AnswerStatus.GROUNDED
    assert turn.grounded.citations == []
    assert len(turn.document_sources) == 1
    assert turn.document_sources[0].document_id == 7


async def test_live_trusted_rag_answer_stops_before_post_tool_model_call() -> None:
    sink = TrustedAnswerSink()
    graph = TrustedAnswerStreamingGraph(sink)
    events: list[str] = []

    async def capture(event_type: str, _payload: dict) -> None:
        events.append(event_type)

    turn = await run_readonly_primary_turn(
        graph=graph,
        ledger=type("Ledger", (), {"events": []})(),
        snapshot=type("Snapshot", (), {})(),
        user_message="What does this project do?",
        trusted_answer_sink=sink,
        event_callback=capture,
    )

    assert graph.post_tool_model_requested is False
    assert turn.grounded.content == "The complete RAG pipeline answered this directly."
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.used_fallback is False
    assert turn.document_sources[0].filename == "01_PROJECT_OVERVIEW.md"
    assert "tool.completed" in events


async def test_trusted_answer_sink_with_no_sources_is_no_evidence() -> None:
    sink = TrustedAnswerSink(
        answer=TrustedRagAnswer(content="No relevant documentation was found.")
    )
    graph = StaticGraph([AIMessage(content="ignored model chatter")])
    turn = await run_readonly_primary_turn(
        graph=graph,
        ledger=type("Ledger", (), {"events": []})(),
        snapshot=type("Snapshot", (), {})(),
        user_message="A question with no indexed evidence",
        trusted_answer_sink=sink,
    )
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.used_fallback is False
    assert turn.grounded.answer_status is AnswerStatus.NO_EVIDENCE
    assert turn.document_sources == []


async def test_empty_trusted_answer_sink_does_not_change_existing_behavior() -> None:
    """A ``None``/empty sink is a pure regression guard: behavior is unchanged."""

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
        trusted_answer_sink=TrustedAnswerSink(),
    )
    assert turn.grounding_verdict is GroundingVerdict.VALID
    assert turn.executor_read_calls == 0
    assert turn.used_fallback is False
    assert "help" in turn.grounded.content
    assert turn.document_sources == []
