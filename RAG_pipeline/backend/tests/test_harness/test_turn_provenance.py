"""Flat per-message provenance: projection rules, safety, and persistence."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import Conversation, ConversationMessage, User
from src.harness.agent_chat_runtime import (
    PrimaryTurnOutcome,
    _get_or_create_assistant_message,
)
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentLoopResult,
    AgentStopReason,
)
from src.harness.capability_runtime import CapabilityObservation
from src.harness.cohort import CohortDecision, RuntimeCohort
from src.harness.evidence_summary import EvidenceSummary, build_evidence_summary
from src.harness.response_synthesis import render_typed_response
from src.harness.turn_provenance import (
    TurnProvenance,
    build_runtime_capability_summary,
    project_turn_provenance,
    resolve_primary_source_layer,
    safe_project_turn_provenance,
)
from src.schemas import DocumentSource


def _topology_observation() -> CapabilityObservation:
    return CapabilityObservation(
        capability_id="topology.landscape.read_structure",
        status="succeeded",
        evidence_refs=[
            {"evidence_id": "topology:p1", "source_type": "topology_structure"},
            {"evidence_id": "topology:p2", "source_type": "topology_structure"},
        ],
        safe_details={"truncated": True},
    )


def _document_source(*, source_layer: str | None, score: float) -> DocumentSource:
    return DocumentSource(
        document_id=1,
        filename="proj/design.md",
        score=score,
        text="body",
        reference_number=1,
        source_layer=source_layer,
    )


class TestRuntimeToolSummary:
    def test_capability_calls_are_aggregated_per_status(self) -> None:
        summary = build_runtime_capability_summary(
            [
                _topology_observation(),
                CapabilityObservation(
                    capability_id="rag.docs.answer_question",
                    status="succeeded",
                    evidence_refs=[{"evidence_id": "doc:1", "source_type": "rag_document"}],
                ),
                CapabilityObservation(
                    capability_id="rag.docs.answer_question",
                    status="succeeded",
                    evidence_refs=[{"evidence_id": "doc:2", "source_type": "rag_document"}],
                ),
                CapabilityObservation(
                    capability_id="runtime.stored.query_evidence",
                    status="failed",
                    error_code="unavailable",
                ),
            ]
        )

        assert summary == [
            {
                "capability_id": "topology.landscape.read_structure",
                "status": "succeeded",
                "call_count": 1,
                "evidence_count": 2,
                "truncated": True,
            },
            {
                "capability_id": "rag.docs.answer_question",
                "status": "succeeded",
                "call_count": 2,
                "evidence_count": 2,
            },
            {
                "capability_id": "runtime.stored.query_evidence",
                "status": "failed",
                "call_count": 1,
                "error_code": "unavailable",
            },
        ]

    def test_turn_without_capability_calls_summarizes_to_null(self) -> None:
        assert build_runtime_capability_summary([]) is None

    def test_entries_are_bounded(self) -> None:
        summary = build_runtime_capability_summary(
            [
                CapabilityObservation(capability_id=f"nexus.cap_{index}", status="succeeded")
                for index in range(30)
            ]
        )

        assert summary is not None
        assert len(summary) == 12

    def test_no_arguments_credentials_or_provider_prose_are_emitted(self) -> None:
        summary = build_runtime_capability_summary(
            [
                CapabilityObservation(
                    capability_id="runtime.stored.query_evidence",
                    status="failed",
                    # Every field a handler may fill with untrusted text.
                    error_code="Bearer sk-live-should-never-persist",
                    error_message="authorization: Bearer sk-live-abcdefghijkl failed",
                    summary="called https://provider.internal/v1 with api_key=hunter2",
                    data={"arguments": {"password": "hunter2"}},
                    safe_details={"token": "sk-live-abcdefghijkl"},
                ),
            ]
        )

        assert summary == [
            {
                "capability_id": "runtime.stored.query_evidence",
                "status": "failed",
                "call_count": 1,
            }
        ]
        rendered = repr(summary)
        for secret in ("hunter2", "sk-live", "Bearer", "provider.internal", "password"):
            assert secret not in rendered


class TestPrimarySourceLayer:
    def test_cited_documents_resolve_to_their_corpus_layer(self) -> None:
        layer = resolve_primary_source_layer(
            [
                _document_source(source_layer="canonical_docs", score=0.71),
                _document_source(source_layer="technical_analysis", score=0.93),
            ],
            EvidenceSummary(),
        )

        assert layer == "technical_analysis"

    def test_document_without_a_layer_payload_resolves_to_canonical_docs(self) -> None:
        layer = resolve_primary_source_layer(
            [_document_source(source_layer=None, score=0.8)], EvidenceSummary()
        )

        assert layer == "canonical_docs"

    def test_non_document_turn_resolves_to_the_dominant_evidence_family(self) -> None:
        summary = build_evidence_summary([_topology_observation()])

        assert resolve_primary_source_layer([], summary) == "topology"

    def test_turn_without_evidence_is_not_labelled(self) -> None:
        assert resolve_primary_source_layer([], EvidenceSummary()) is None

    def test_tied_families_are_not_labelled(self) -> None:
        summary = build_evidence_summary(
            [
                CapabilityObservation(
                    capability_id="topology.landscape.read_structure",
                    status="succeeded",
                    evidence_refs=[
                        {"evidence_id": "topology:p1", "source_type": "topology_structure"}
                    ],
                ),
                CapabilityObservation(
                    capability_id="runtime.stored.query_evidence",
                    status="succeeded",
                    evidence_refs=[
                        {"evidence_id": "run:1", "source_type": "runtime_incident"}
                    ],
                ),
            ]
        )

        assert resolve_primary_source_layer([], summary) is None

    def test_a_layer_that_would_not_fit_the_column_is_dropped(self) -> None:
        layer = resolve_primary_source_layer(
            [_document_source(source_layer="x" * 51, score=0.9)], EvidenceSummary()
        )

        assert layer == "canonical_docs"


class TestProjection:
    def test_documentation_failover_sets_fallback_used(self) -> None:
        provenance = project_turn_provenance(
            observations=[_topology_observation()],
            document_sources=[],
            evidence_summary=build_evidence_summary(
                [_topology_observation()],
                documentation_trace={
                    "provider_used": "openai",
                    "fallback_used": True,
                    "attempts": [
                        {"provider": "kimi", "status": "failed"},
                        {"provider": "openai", "status": "succeeded"},
                    ],
                },
            ),
        )

        assert provenance.primary_source_layer == "topology"
        assert provenance.fallback_used is True

    def test_projection_failure_degrades_to_nulls_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def explode(observations):  # noqa: ANN001, ANN202
            raise RuntimeError("projection defect")

        monkeypatch.setattr(
            "src.harness.turn_provenance.build_runtime_capability_summary", explode
        )
        with caplog.at_level(logging.ERROR, logger="src.harness.turn_provenance"):
            provenance = safe_project_turn_provenance(
                observations=[_topology_observation()],
                document_sources=[],
                evidence_summary=EvidenceSummary(),
            )

        assert provenance == TurnProvenance()
        assert "Turn provenance projection failed" in caplog.text
        assert "projection defect" in caplog.text


async def _persisted_message(
    outcome: PrimaryTurnOutcome, tmp_path, name: str
) -> ConversationMessage:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        user = User(username="provenance", email="provenance@example.com")
        session.add(user)
        await session.flush()
        conversation = Conversation(title="Provenance", user_id=user.id)
        session.add(conversation)
        await session.flush()
        user_message = ConversationMessage(
            conversation_id=conversation.id, role="user", content="What feeds this job?"
        )
        session.add(user_message)
        await session.flush()
        await _get_or_create_assistant_message(
            db=session,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            outcome=outcome,
            latency_ms=1234,
        )
        await session.commit()
        return (
            await session.execute(
                select(ConversationMessage).where(
                    ConversationMessage.in_reply_to_message_id == user_message.id
                )
            )
        ).scalar_one()


def _outcome(
    *,
    observations: list[CapabilityObservation],
    document_sources: list[DocumentSource],
    evidence_summary: EvidenceSummary,
) -> PrimaryTurnOutcome:
    return PrimaryTurnOutcome(
        result=AgentLoopResult(
            action=AgentBoundaryAction.ANSWER,
            output="The job is fed by two upstream flows.",
            stop_reason=AgentStopReason.ANSWERED,
            state_version="state-v1",
            observations=observations,
            typed_response=render_typed_response(
                action=AgentBoundaryAction.ANSWER.value,
                content="The job is fed by two upstream flows.",
                plan=None,
                facts=[],
                state_version="state-v1",
                stop_reason=AgentStopReason.ANSWERED.value,
            ),
        ),
        grounded=None,
        cohort=CohortDecision(
            cohort=RuntimeCohort.V2_EFFECT_PRIMARY,
            cohort_source="test",
            cohort_bucket=1,
            reason_code="test",
        ),
        document_sources=document_sources,
        evidence_summary=evidence_summary,
    )


async def test_assistant_message_lands_with_populated_provenance(tmp_path) -> None:
    observations = [_topology_observation()]
    message = await _persisted_message(
        _outcome(
            observations=observations,
            document_sources=[],
            evidence_summary=build_evidence_summary(observations),
        ),
        tmp_path,
        "provenance-populated.sqlite3",
    )

    assert message.primary_source_layer == "topology"
    assert message.fallback_used is False
    assert message.doc_gap is False
    assert message.runtime_tool_summary == [
        {
            "capability_id": "topology.landscape.read_structure",
            "status": "succeeded",
            "call_count": 1,
            "evidence_count": 2,
            "truncated": True,
        }
    ]


async def test_turn_without_tool_calls_persists_truthful_nulls(tmp_path) -> None:
    message = await _persisted_message(
        _outcome(observations=[], document_sources=[], evidence_summary=EvidenceSummary()),
        tmp_path,
        "provenance-empty.sqlite3",
    )

    assert message.runtime_tool_summary is None
    assert message.primary_source_layer is None
    assert message.fallback_used is False
    assert message.content == "The job is fed by two upstream flows."


async def test_projection_defect_does_not_cost_the_user_the_answer(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(**kwargs):  # noqa: ANN003, ANN202
        raise RuntimeError("projection defect")

    monkeypatch.setattr("src.harness.turn_provenance.project_turn_provenance", explode)
    observations = [_topology_observation()]
    with caplog.at_level(logging.ERROR, logger="src.harness.turn_provenance"):
        message = await _persisted_message(
            _outcome(
                observations=observations,
                document_sources=[],
                evidence_summary=build_evidence_summary(observations),
            ),
            tmp_path,
            "provenance-degraded.sqlite3",
        )

    assert message.content == "The job is fed by two upstream flows."
    assert message.structured_answer is not None
    assert message.runtime_tool_summary is None
    assert message.primary_source_layer is None
    assert "Turn provenance projection failed" in caplog.text
