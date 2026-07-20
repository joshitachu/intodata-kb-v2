from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.harness import evidence as evidence_module
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentLoopInput,
    AgentModelResult,
    BoundedAgentLoop,
    non_stream_transport,
    registered_capability_manifest,
    stream_transport,
)
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
)
from src.harness.evidence import (
    build_registered_read_adapters,
    conversation_summary_facts,
    read_document_evidence,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import build_runtime_capability_manifest
from src.harness.models import (
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    LOG_SEARCH_EVIDENCE_CAPABILITY,
    METADATA_QUERY_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.registry import base_capability_registry, default_capability_registry
from src.schemas import Source


MIGRATED_READ_IDS = {
    DOCUMENT_SEARCH_CAPABILITY.id,
    DOCUMENT_READ_CAPABILITY.id,
    DOCUMENT_LIST_CAPABILITY.id,
    METADATA_QUERY_CAPABILITY.id,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
    LOG_SEARCH_EVIDENCE_CAPABILITY.id,
    CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
}
BOUNDED_ONLY_IDS = {
    DOCUMENT_READ_CAPABILITY.id,
    METADATA_QUERY_CAPABILITY.id,
    CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
}


@dataclass
class ScriptedModel:
    decisions: list[AgentDecision]
    requests: list = field(default_factory=list)

    async def decide(self, request):  # noqa: ANN001
        self.requests.append(request)
        return AgentModelResult(decision=self.decisions[len(self.requests) - 1])


def _call(capability_id: str, arguments: dict) -> AgentDecision:
    return AgentDecision(
        action=AgentBoundaryAction.CALL_CAPABILITY,
        capability_id=capability_id,
        arguments=arguments,
    )


def _answer(content: str) -> AgentDecision:
    return AgentDecision(action=AgentBoundaryAction.ANSWER, content=content)


def _context(**updates) -> CapabilityExecutionContext:
    values = {
        "user_id": 7,
        "conversation_id": 41,
        "project_scope": ["alpha"],
    }
    values.update(updates)
    return CapabilityExecutionContext(**values)


def test_production_registry_exposes_every_slice_4_read_as_registered() -> None:
    registry = base_capability_registry()

    assert MIGRATED_READ_IDS.issubset({item.id for item in registry.list()})
    assert all(registry.handler_for(item).capability_id == item for item in MIGRATED_READ_IDS)


def test_pack_compiled_agent_manifest_grants_new_reads_without_legacy_leakage() -> None:
    registry = default_capability_registry()
    legacy_manifest = build_runtime_capability_manifest(registry)
    agent_manifest = registered_capability_manifest(registry, project_ids=["alpha"])

    assert MIGRATED_READ_IDS.issubset({item.id for item in agent_manifest.tools})
    assert not BOUNDED_ONLY_IDS.intersection({item.id for item in legacy_manifest.tools})


async def test_metadata_search_read_grounded_turn_uses_registered_handlers() -> None:
    calls: list[str] = []

    async def metadata(_arguments, _context):  # noqa: ANN001
        calls.append(METADATA_QUERY_CAPABILITY.id)
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="metadata:project:alpha",
                    capability_id=METADATA_QUERY_CAPABILITY.id,
                    source_type="structured_metadata",
                    title="Alpha",
                    summary="Alpha has indexed documentation.",
                    metadata={"project_id": "alpha", "document_id": 19},
                )
            ],
        )

    async def search(_arguments, _context):  # noqa: ANN001
        calls.append(DOCUMENT_SEARCH_CAPABILITY.id)
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="doc:19:2",
                    capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                    source_type="rag_document",
                    title="Alpha security",
                    summary="The scanner records CVE findings.",
                    uri="/projects/alpha/docs/security.md",
                    score=0.91,
                    metadata={
                        "project_id": "alpha",
                        "document_id": 19,
                        "chunk_index": 2,
                    },
                )
            ],
        )

    async def read(_arguments, _context):  # noqa: ANN001
        calls.append(DOCUMENT_READ_CAPABILITY.id)
        return EvidenceQueryResult(
            capability_id=DOCUMENT_READ_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="doc:19:4",
                    capability_id=DOCUMENT_READ_CAPABILITY.id,
                    source_type="rag_document_passage",
                    title="Alpha security",
                    summary="Critical findings are blocked before publication.",
                    uri="/projects/alpha/docs/security.md",
                    score=0.95,
                    metadata={
                        "project_id": "alpha",
                        "document_id": 19,
                        "chunk_index": 4,
                    },
                )
            ],
        )

    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={
            METADATA_QUERY_CAPABILITY.id: metadata,
            DOCUMENT_SEARCH_CAPABILITY.id: search,
            DOCUMENT_READ_CAPABILITY.id: read,
        },
    )
    model = ScriptedModel(
        [
            _call(METADATA_QUERY_CAPABILITY.id, {"query_type": "project_detail", "project_id": "alpha"}),
            _call(DOCUMENT_SEARCH_CAPABILITY.id, {"query": "security findings", "project_id": "alpha", "top_k": 5}),
            _call(DOCUMENT_READ_CAPABILITY.id, {"query": "publication rule", "project_id": "alpha", "document_id": 19}),
            _answer(
                "Alpha blocks critical findings before publication "
                "[metadata:project:alpha] [doc:19:4]."
            ),
        ]
    )

    result = await BoundedAgentLoop(
        registry=registry,
        executor=executor,
        model=model,
    ).run(AgentLoopInput(user_message="What does Alpha publish?"), context=_context())

    assert calls == [
        METADATA_QUERY_CAPABILITY.id,
        DOCUMENT_SEARCH_CAPABILITY.id,
        DOCUMENT_READ_CAPABILITY.id,
    ]
    assert result.stop_reason == "answered"
    assert "Critical findings are blocked before publication" in result.output
    assert result.typed_response is not None
    assert {item["id"] for item in result.typed_response.evidence_refs} >= {
        "metadata:project:alpha",
        "doc:19:4",
    }
    assert [item.capability_id for item in result.observations] == calls
    assert result.observations[-1].evidence_refs == [
        {
            "evidence_id": "doc:19:4",
            "source_type": "rag_document_passage",
            "project_id": "alpha",
            "document_id": 19,
            "chunk_index": 4,
            "uri": "/projects/alpha/docs/security.md",
            "score": 0.95,
        }
    ]
    streamed = [event async for event in stream_transport(result)]
    assert non_stream_transport(result)["trace"] == [
        json.loads(payload) for event, payload in streamed if event == "agent_trace"
    ]


async def test_project_and_evidence_policy_denial_never_invokes_backend() -> None:
    calls = 0

    async def backend(_arguments, _context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return []

    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: backend},
    )
    request = CapabilityExecutionRequest(
        capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
        arguments={"query": "private", "project_id": "project-b"},
    )

    wrong_project = await executor.execute(request, _context())
    denied_class = await executor.execute(
        request.model_copy(update={"arguments": {"query": "private", "project_id": "alpha"}}),
        _context(denied_capability_ids={DOCUMENT_SEARCH_CAPABILITY.id}),
    )

    assert wrong_project.status == "denied"
    assert denied_class.status == "denied"
    assert calls == 0


async def test_registered_runtime_read_is_deterministic_and_never_nests_agent(
    monkeypatch, db_session
) -> None:
    async def runtime_sources(_self, params, context):  # noqa: ANN001
        assert params.project_id == "alpha"
        assert context.project_scope == ["alpha"]
        return [
            {
                "project_id": "alpha",
                "source_key": "stored-alpha",
                "name": "Alpha runtime",
                "status": "success",
                "last_seen_at": "2026-07-18T20:00:00Z",
            }
        ]

    monkeypatch.setattr(
        evidence_module.StoredRuntimeProvider,
        "list_runtime_sources",
        runtime_sources,
    )
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
    )
    registry = base_capability_registry()
    result = await HarnessCapabilityExecutor(
        registry,
        evidence_handlers=adapters,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={
                "operation": "list_runtime_sources",
                "project_id": "alpha",
                "limit": 5,
            },
        ),
        _context(),
    )

    assert result.status == "succeeded"
    assert result.data["items"][0]["metadata"]["record_ref"] == "stored-alpha"
    assert result.evidence_refs[0]["runtime_record_id"] == "stored-alpha"


async def test_final_observation_projection_redacts_secrets_and_internal_paths() -> None:
    async def unsafe_provider(_arguments, _context):  # noqa: ANN001
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            answer_draft="token=top-secret sk-abcdefghijklmnop",
            items=[
                EvidenceItem(
                    id="doc:1:1",
                    capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                    source_type="rag_document",
                    title="password=hunter2",
                    summary="api_key=very-secret sk-abcdefghijklmnop",
                    uri="/admin/runtime?token=sk-abcdefghijklmnop",
                    metadata={
                        "project_id": "alpha",
                        "document_id": 1,
                        "chunk_index": 1,
                        "filename": "/private/tmp/provider-artifact.md",
                        "internal_payload": "token=do-not-leak",
                    },
                )
            ],
        )

    registry = base_capability_registry()
    observation = await HarnessCapabilityExecutor(
        registry,
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: unsafe_provider},
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            arguments={"query": "secrets", "project_id": "alpha"},
        ),
        _context(),
    )

    serialized = observation.model_dump_json()
    assert "hunter2" not in serialized
    assert "very-secret" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "/private/tmp" not in serialized
    assert "internal_payload" not in serialized
    assert observation.data["items"][0]["uri"] == "/admin/runtime"
    assert observation.data["items"][0]["metadata"]["filename"] == "provider-artifact.md"


def test_conversation_summary_uses_real_ids_bounds_and_redacts() -> None:
    history = [
        {"message_id": index, "role": "user", "content": f"message {index}"}
        for index in range(1, 25)
    ]
    history[-1]["content"] = "token=private-value final decision"

    result = conversation_summary_facts(
        history=history,
        conversation_id=41,
        max_messages=3,
    )

    assert [item.id for item in result.items] == [
        "conversation:41:message:22",
        "conversation:41:message:23",
        "conversation:41:message:24",
    ]
    assert result.items[-1].metadata["message_id"] == 24
    assert "private-value" not in result.items[-1].summary


async def test_document_read_preserves_relevance_and_server_scope(monkeypatch) -> None:
    seen: dict = {}

    async def search(**kwargs):
        seen.update(kwargs)
        return [
            Source(
                document_id=19,
                filename="security.md",
                chunk_index=7,
                score=0.93,
                text="The relevant security control.",
                project_id="alpha",
            )
        ]

    monkeypatch.setattr(evidence_module.settings, "reranker_enabled", False)
    monkeypatch.setattr(evidence_module, "search_similar", search)

    result = await read_document_evidence(
        qdrant=object(),  # type: ignore[arg-type]
        query="security control",
        user_groups=["engineering"],
        project_id="alpha",
        document_id=19,
        max_chunks=3,
    )

    assert seen["user_groups"] == ["engineering"]
    assert seen["project_ids"] == ["alpha"]
    assert seen["document_ids"] == [19]
    assert result.items[0].score == 0.93
    assert result.items[0].metadata["chunk_index"] == 7


async def test_empty_provider_failure_and_missing_adapter_are_typed() -> None:
    async def empty(_arguments, _context):  # noqa: ANN001
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    async def unavailable(_arguments, _context):  # noqa: ANN001
        raise OSError("provider payload must not escape")

    async def failed_result(_arguments, _context):  # noqa: ANN001
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="failed",
            error_message="Bearer abcdefghijklmnop token=provider-secret",
        )

    registry = base_capability_registry()
    request = CapabilityExecutionRequest(
        capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
        arguments={"query": "anything", "project_id": "alpha"},
    )
    empty_result = await HarnessCapabilityExecutor(
        registry,
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: empty},
    ).execute(request, _context())
    unavailable_result = await HarnessCapabilityExecutor(
        registry,
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: unavailable},
    ).execute(request, _context())
    failed_result_observation = await HarnessCapabilityExecutor(
        registry,
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: failed_result},
    ).execute(request, _context())
    missing_result = await HarnessCapabilityExecutor(registry).execute(request, _context())

    assert empty_result.status == "succeeded"
    assert empty_result.safe_details == {"evidence_count": 0}
    assert unavailable_result.status == "failed" and unavailable_result.retryable
    assert "provider payload" not in unavailable_result.model_dump_json()
    assert "provider-secret" not in failed_result_observation.model_dump_json()
    assert "abcdefghijklmnop" not in failed_result_observation.model_dump_json()
    assert missing_result.status == "failed"
    assert missing_result.error_code == "capability_handler_unavailable"
