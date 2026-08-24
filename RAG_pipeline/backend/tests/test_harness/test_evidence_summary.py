from src.harness.capability_runtime import CapabilityObservation
from src.harness.evidence_summary import build_evidence_summary


def test_builds_fused_current_turn_summary_and_provider_metadata() -> None:
    summary = build_evidence_summary(
        [
            CapabilityObservation(
                capability_id="topology.landscape.read_structure",
                status="succeeded",
                evidence_refs=[{"evidence_id": "topology:p1", "source_type": "topology_structure"}],
            ),
            CapabilityObservation(
                capability_id="rag.docs.answer_question",
                status="succeeded",
                evidence_refs=[
                    {"evidence_id": "doc:1", "source_type": "rag_document"},
                    {"evidence_id": "doc:2", "source_type": "rag_document"},
                ],
            ),
            CapabilityObservation(
                capability_id="runtime.stored.query_evidence",
                status="failed",
                error_code="unavailable",
                error_message="credential=must-not-persist",
            ),
        ],
        documentation_trace={
            "provider_used": "openai",
            "fallback_used": True,
            "attempts": [
                {"provider": "kimi", "status": "failed"},
                {"provider": "openai", "status": "succeeded"},
            ],
        },
    )

    assert summary.evidence_step_count == 3
    assert summary.zero_evidence is False
    assert [item.model_dump() for item in summary.successful_sources] == [
        {"family": "documentation", "role": "explanation", "evidence_count": 2},
        {"family": "topology", "role": "structure", "evidence_count": 1},
    ]
    assert summary.failed_sources[0].family == "runtime"
    assert summary.failed_sources[0].status == "unavailable"
    assert "must-not-persist" not in summary.model_dump_json()
    assert summary.documentation.provider_used == "openai"
    assert summary.documentation.fallback_used is True


def test_zero_evidence_and_failed_attempt_do_not_count_as_evidence() -> None:
    summary = build_evidence_summary(
        [
            CapabilityObservation(
                capability_id="rag.docs.answer_question",
                status="failed",
                error_code="handler_failure",
            ),
            CapabilityObservation(
                capability_id="topology.landscape.read_structure",
                status="succeeded",
                evidence_refs=[],
            ),
        ],
        documentation_trace={
            "provider_used": None,
            "fallback_used": False,
            "attempts": [{"provider": "kimi", "status": "failed"}],
        },
    )

    assert summary.evidence_step_count == 0
    assert summary.successful_sources == []
    assert summary.zero_evidence is True
    assert [item.model_dump() for item in summary.failed_sources] == [
        {
            "family": "documentation",
            "status": "unavailable",
            "reason_code": "providers_exhausted",
            "message": "Documentation is temporarily unavailable.",
        }
    ]


def test_new_turn_does_not_inherit_prior_failure_or_evidence() -> None:
    first = build_evidence_summary(
        [
            CapabilityObservation(
                capability_id="runtime.logs.search_evidence",
                status="failed",
                error_code="handler_failure",
            )
        ]
    )
    second = build_evidence_summary([])

    assert first.failed_sources
    assert second.failed_sources == []
    assert second.successful_sources == []
    assert second.zero_evidence is True

