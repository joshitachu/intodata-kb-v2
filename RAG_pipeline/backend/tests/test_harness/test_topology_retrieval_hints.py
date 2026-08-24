from __future__ import annotations

from src.harness import evidence
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.evidence import build_registered_read_adapters
from src.harness.models import DOCUMENTATION_ANSWER_CAPABILITY, EvidenceQueryResult
from src.harness.trusted_answer import TrustedAnswerSink
from src.retrieval.topology_hints import TopologyHintStrength, TopologyRetrievalHints


async def test_document_adapter_resolves_hints_with_exact_scope_and_request_token(
    monkeypatch,
    db_session,
) -> None:
    resolved: dict[str, object] = {}
    answered: dict[str, object] = {}
    hint = TopologyRetrievalHints(
        project_id="alpha",
        strength=TopologyHintStrength.EXACT,
        matched_entity_names=("Maximo",),
        retrieval_terms=("Maximo",),
    )

    async def resolve(**kwargs):  # noqa: ANN003, ANN202
        resolved.update(kwargs)
        return hint

    async def answer(**kwargs):  # noqa: ANN003, ANN202
        answered.update(kwargs)
        return EvidenceQueryResult(
            capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
            status="succeeded",
            items=[],
            answer_draft="Grounded answer.",
        )

    monkeypatch.setattr(evidence, "resolve_topology_retrieval_hints", resolve)
    monkeypatch.setattr(evidence, "answer_document_question_evidence", answer)
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    await adapters[DOCUMENTATION_ANSWER_CAPABILITY.id](
        {"query": "What does Maximo do?", "project_id": "alpha"},
        CapabilityExecutionContext(
            user_id=7,
            conversation_id=41,
            project_scope=["alpha"],
            access_token="request-token",
        ),
    )

    assert resolved == {
        "query": "What does Maximo do?",
        "project_ids": ["alpha"],
        "access_token": "request-token",
    }
    assert answered["project_ids"] == ["alpha"]
    assert answered["topology_hints"] is hint


async def test_document_adapter_never_uses_model_project_outside_authorized_scope(
    monkeypatch,
    db_session,
) -> None:
    resolved: dict[str, object] = {}
    answered: dict[str, object] = {}

    async def resolve(**kwargs):  # noqa: ANN003, ANN202
        resolved.update(kwargs)
        return None

    async def answer(**kwargs):  # noqa: ANN003, ANN202
        answered.update(kwargs)
        return EvidenceQueryResult(
            capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    monkeypatch.setattr(evidence, "resolve_topology_retrieval_hints", resolve)
    monkeypatch.setattr(evidence, "answer_document_question_evidence", answer)
    adapters = build_registered_read_adapters(
        qdrant=object(),  # type: ignore[arg-type]
        db=db_session,
        user_groups=["engineering"],
        docs_provider=object(),  # type: ignore[arg-type]
        conversation_history=[],
        conversation_id=41,
        trusted_answer_sink=TrustedAnswerSink(),
    )

    await adapters[DOCUMENTATION_ANSWER_CAPABILITY.id](
        {"query": "Explain private", "project_id": "private"},
        CapabilityExecutionContext(
            user_id=7,
            conversation_id=41,
            project_scope=["alpha"],
            access_token="request-token",
        ),
    )

    assert resolved["project_ids"] == []
    assert answered["project_ids"] == []
    assert answered["topology_hints"] is None

