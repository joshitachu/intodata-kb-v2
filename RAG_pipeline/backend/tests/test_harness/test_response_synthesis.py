from __future__ import annotations

import datetime

from src.harness.capability_runtime import CapabilityObservation
from src.harness.response_synthesis import (
    ResponseFactKind,
    ResponseSynthesisPlan,
    SynthesisSource,
    build_response_fact_catalog,
    factual_fingerprint,
    render_typed_response,
)
from src.harness.workflow_projection import WorkflowCard


def test_observation_facts_are_immutable_and_model_content_cannot_replace_them() -> None:
    observation = CapabilityObservation(
        capability_id="nexus.workflows.cancel",
        status="succeeded",
        summary="The workflow had already failed.",
        data={
            "outcome": "already_failed",
            "workflow_status": "failed",
            "token": "must-not-render",
        },
        workflow_ref={"workflow_run_id": 166, "status": "failed"},
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    mandatory = [fact.fact_id for fact in facts if fact.mandatory]
    response = render_typed_response(
        action="answer",
        content="It succeeded and is still running.",
        plan=ResponseSynthesisPlan(fact_ids=mandatory),
        facts=facts,
        state_version="v1",
        stop_reason="answered",
        observations=[observation],
    )

    assert "already failed" in response.content
    assert "still running" not in response.content
    assert response.synthesis_source is SynthesisSource.AGENT_PLAN
    assert observation.data["workflow_status"] == "failed"
    assert "must-not-render" not in response.model_dump_json()


def test_invalid_fact_plan_uses_deterministic_fallback_without_dropping_mandatory_fact() -> None:
    observation = CapabilityObservation(
        capability_id="mcp.docs.generation_status",
        status="failed",
        summary="Status could not be refreshed.",
        error_message="The provider is unavailable.",
        retryable=True,
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="clarify",
        content="Everything is fine.",
        plan=ResponseSynthesisPlan(fact_ids=[]),
        facts=facts,
        state_version="v2",
        stop_reason="clarification_required",
        observations=[observation],
    )

    assert response.synthesis_source is SynthesisSource.DETERMINISTIC_FALLBACK
    assert "Status could not be refreshed" in response.content
    assert "provider is unavailable" in response.content
    assert "Everything is fine" not in response.content


def test_requested_input_is_authoritative_clarification_not_generic_workflow_text() -> None:
    facts = build_response_fact_catalog(
        observations=[],
        conversation_state={
            "requested_inputs": [
                {
                    "field": "platform",
                    "prompt": "Which scanner platform should this project use?",
                    "options": ["generic", "talend"],
                }
            ]
        },
        workflow_state=[],
    )
    response = render_typed_response(
        action="clarify",
        content="Choose the pending workflow action shown above.",
        plan=None,
        facts=facts,
        state_version="v3",
        stop_reason="clarification_required",
    )

    assert "Which scanner platform" in response.content
    assert "generic, talend" in response.content
    assert "pending workflow action" not in response.content


def test_existing_workflow_state_is_a_fact_without_a_fresh_observation() -> None:
    facts = build_response_fact_catalog(
        observations=[],
        conversation_state={"active_tasks": [{"workflow_run_id": 44, "focused": True}]},
        workflow_state=[
            {
                "workflow_run_id": 44,
                "project_id": "alpha",
                "status": "running",
                "output_summary": None,
                "error_message": None,
            }
        ],
    )

    assert [(fact.fact_id, fact.value) for fact in facts] == [("workflow:44:status", "running")]
    assert facts[0].mandatory is True
    response = render_typed_response(
        action="answer",
        content="No workflows are active.",
        plan=ResponseSynthesisPlan(fact_ids=["workflow:44:status"]),
        facts=facts,
        state_version="v4",
        stop_reason="answered",
    )
    assert "Workflow for alpha: Running" in response.content
    assert "No workflows" not in response.content


def test_terminal_historical_workflow_does_not_override_unrelated_taskless_answer() -> None:
    facts = build_response_fact_catalog(
        observations=[],
        conversation_state={"active_tasks": [], "requested_inputs": []},
        workflow_state=[
            {
                "workflow_run_id": 166,
                "project_id": "old-project",
                "status": "failed",
                "error_message": "Historical failure.",
            }
        ],
    )
    response = render_typed_response(
        action="answer",
        content="A deployment manifest describes the desired application state.",
        plan=None,
        facts=facts,
        state_version="historical",
        stop_reason="answered",
    )

    assert facts == []
    assert response.content.startswith("A deployment manifest")
    assert "Historical failure" not in response.content
    assert "pending workflow action" not in response.content


def test_active_workflow_status_and_error_are_mandatory_when_plan_is_empty() -> None:
    facts = build_response_fact_catalog(
        observations=[],
        conversation_state={"active_tasks": [{"workflow_run_id": 44}]},
        workflow_state=[
            {
                "workflow_run_id": 44,
                "project_id": "alpha",
                "status": "failed",
                "error_message": "The exact provider child failed.",
            }
        ],
    )
    response = render_typed_response(
        action="answer",
        content="Everything is fine.",
        plan=ResponseSynthesisPlan(fact_ids=[]),
        facts=facts,
        state_version="active-error",
        stop_reason="answered",
    )

    assert {fact.fact_id for fact in facts if fact.mandatory} == {
        "workflow:44:status",
        "workflow:44:summary",
    }
    assert response.synthesis_source is SynthesisSource.DETERMINISTIC_FALLBACK
    assert "Failed" in response.content
    assert "exact provider child failed" in response.content
    assert "Everything is fine" not in response.content


def test_factual_fingerprint_ignores_presentation_order_and_style() -> None:
    facts = build_response_fact_catalog(
        observations=[
            CapabilityObservation(
                capability_id="read",
                status="succeeded",
                summary="Found two documents.",
                data={"document_count": 2},
            )
        ],
        conversation_state={},
        workflow_state=[],
    )
    reversed_facts = list(reversed(facts))

    assert factual_fingerprint(facts, state_version="authority") == factual_fingerprint(
        reversed_facts, state_version="authority"
    )
    assert factual_fingerprint(facts, state_version="authority") != factual_fingerprint(
        facts, state_version="new-authority"
    )


def test_public_fact_catalog_filters_secret_keys_and_values() -> None:
    facts = build_response_fact_catalog(
        observations=[
            CapabilityObservation(
                capability_id="read",
                status="succeeded",
                output_type="documentation_generation_status",
                data={
                    "project_id": "alpha",
                    "api_key": "sk-secret-value",
                    "note": "Authorization: Bearer hidden-value",
                    "external_run_id": "external-secret-id",
                    "receipt_id": "receipt-secret-id",
                    "nested": {"unknown": "must-not-render"},
                },
            )
        ],
        conversation_state={},
        workflow_state=[],
    )
    rendered = " ".join(f"{fact.label} {fact.value}" for fact in facts)

    assert "alpha" in rendered
    assert "secret" not in rendered
    assert "Bearer" not in rendered
    assert "external" not in rendered
    assert "receipt" not in rendered
    assert "must-not-render" not in rendered
    assert any(fact.kind is ResponseFactKind.OBSERVATION_STATUS for fact in facts)


def test_summary_error_links_and_evidence_are_redacted_or_rejected() -> None:
    observation = CapabilityObservation(
        capability_id="read",
        status="failed",
        output_type="documentation_generation_status",
        summary="Failed with Authorization: Bearer summary-secret",
        error_message="api_key=error-secret",
        artifact_links=[
            {"label": "Unsafe", "url": "/docs?access_token=link-secret"},
            {"label": "Docs", "url": "/docs", "kind": "token=kind-secret"},
        ],
        evidence_refs=[
            {"id": "evidence-1", "label": "password=evidence-secret"},
        ],
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="unsafe",
        plan=None,
        facts=facts,
        state_version="secure",
        stop_reason="answered",
        observations=[observation],
    )
    payload = response.model_dump_json()

    assert "summary-secret" not in payload
    assert "error-secret" not in payload
    assert "link-secret" not in payload
    assert "evidence-secret" not in payload
    assert response.artifact_links == [{"label": "Docs", "url": "/docs", "kind": "artifact"}]


def test_cancellation_outcome_and_status_cannot_be_omitted_by_plan() -> None:
    observation = CapabilityObservation(
        capability_id="nexus.workflows.cancel",
        status="succeeded",
        output_type="workflow_cancellation_outcome",
        summary="Cancellation resolved.",
        data={"outcome": "already_failed", "workflow_status": "failed"},
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="Cancelled.",
        plan=ResponseSynthesisPlan(fact_ids=["observation:0:status", "observation:0:summary"]),
        facts=facts,
        state_version="cancel",
        stop_reason="answered",
        observations=[observation],
    )

    assert response.synthesis_source is SynthesisSource.DETERMINISTIC_FALLBACK
    assert "Already failed" in response.content
    assert "Workflow status: Failed" in response.content
    assert "already_failed" not in response.content


def test_slice4_evidence_answer_uses_vetted_content_without_internal_references() -> None:
    observation = CapabilityObservation(
        capability_id="rag.docs.search_evidence",
        status="succeeded",
        output_type="evidence_items",
        summary="Retrieved 1 evidence item(s).",
        data={
            "answer_draft": "Project Alpha uses an event-driven ingestion pipeline.",
            "items": [
                {
                    "id": "internal-evidence-id",
                    "title": "Ingestion architecture",
                    "summary": "Events enter through the managed queue before transformation.",
                    "uri": "/projects/alpha/docs/internal-uri",
                    "score": 0.91,
                    "observed_at": "2026-07-19T01:00:00Z",
                    "source_type": "indexed_documentation",
                    "external_run_id": "external-secret-id",
                    "metadata": {
                        "project_id": "alpha",
                        "source_layer": "generated",
                        "record_ref": "runtime-record-secret",
                        "message_id": 991,
                        "document_id": 42,
                    },
                }
            ],
        },
        evidence_refs=[
            {
                "evidence_id": "evidence-public-1",
                "record_ref": "runtime-record-secret",
            }
        ],
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="Invented architecture answer.",
        plan=None,
        facts=facts,
        state_version="evidence",
        stop_reason="answered",
        observations=[observation],
    )
    payload = response.model_dump_json()

    assert "event-driven ingestion pipeline" in response.content
    assert "managed queue" in response.content
    assert "internal-evidence-id" not in payload
    assert "evidence-public-1" in payload
    assert "internal-uri" not in payload
    assert "runtime-record-secret" not in payload
    assert "message_id" not in payload
    assert "external-secret-id" not in payload
    assert "document_id" not in payload
    safe = {(fact.label, fact.value) for fact in response.facts}
    assert ("Project Id", "alpha") in safe
    assert ("Source Layer", "generated") in safe


def test_invalid_plan_fallback_content_is_bounded_to_typed_response_limit() -> None:
    observations = [
        CapabilityObservation(
            capability_id=f"read.{index}",
            status="succeeded",
            output_type="evidence_items",
            summary=f"Evidence group {index}.",
            data={"answer_draft": (f"Answer {index} " + "x" * 7_900)},
        )
        for index in range(10)
    ]
    facts = build_response_fact_catalog(
        observations=observations, conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="ignored",
        plan=None,
        facts=facts,
        state_version="bounded",
        stop_reason="answered",
        observations=observations,
    )

    assert len(response.content) <= 20_000
    assert response.facts


def test_requested_input_field_and_options_are_redacted() -> None:
    facts = build_response_fact_catalog(
        observations=[],
        conversation_state={
            "requested_inputs": [
                {
                    "field": "token=field-secret",
                    "prompt": "Choose safely.",
                    "options": ["talend", "api_key=option-secret"],
                }
            ]
        },
        workflow_state=[],
    )
    payload = " ".join(f"{fact.label} {fact.value}" for fact in facts)

    assert "field-secret" not in payload
    assert "option-secret" not in payload
    assert "talend" in payload


def test_pre_hydration_plan_cannot_omit_mandatory_canonical_card_facts() -> None:
    fact = build_response_fact_catalog(
        observations=[],
        conversation_state={"active_tasks": [{"workflow_run_id": 44}]},
        workflow_state=[{"workflow_run_id": 44, "project_id": "alpha", "status": "running"}],
    )[0]
    card = WorkflowCard(
        workflow_run_id=44,
        project_id="alpha",
        capability_id="mcp.docs.generate_project_documentation",
        label="Documentation generation",
        status="failed",
        display_label="Failed",
        display_tone="danger",
        is_active=False,
        error_message="The exact provider child failed.",
        started_at=datetime.datetime(2026, 7, 19, 1, 0, tzinfo=datetime.UTC),
        finished_at=datetime.datetime(2026, 7, 19, 1, 1, tzinfo=datetime.UTC),
        projected_at=datetime.datetime(2026, 7, 19, 1, 2, tzinfo=datetime.UTC),
        elapsed_ms=60_000,
        timing_complete=True,
    )
    response = render_typed_response(
        action="answer",
        content=None,
        plan=ResponseSynthesisPlan(fact_ids=[fact.fact_id]),
        facts=[fact],
        workflow_cards=[card],
        state_version="hydrated",
        stop_reason="answered",
    )

    assert "Failed" in response.content
    assert "exact provider child failed" in response.content
    assert {item.fact_id for item in response.facts} >= {
        "workflow:44:canonical_status",
        "workflow:44:canonical_error",
    }


def test_conversation_163_shape_renders_natural_active_workflow_facts() -> None:
    observation = CapabilityObservation(
        capability_id="nexus.workflows.active_project_status",
        status="succeeded",
        output_type="active_project_workflows",
        summary="Found 1 active project workflow.",
        data={
            "workflows": [
                {
                    "project_id": "alpha",
                    "display_name": "Project Alpha",
                    "project_status": "ready",
                    "generation_status": "running",
                    "generation_started_at": "2026-07-19T01:00:00Z",
                }
            ]
        },
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="Which time frame?",
        plan=None,
        facts=facts,
        state_version="status-shape",
        stop_reason="answered",
        observations=[observation],
    )

    assert "Project Alpha" in response.content
    assert "Documentation status: Running" in response.content
    assert "Capability result" not in response.content
    assert "generation_status" not in response.content
    assert "time frame" not in response.content


def test_conversation_166_shape_renders_already_failed_without_technical_or_generic_text() -> None:
    observation = CapabilityObservation(
        capability_id="nexus.workflows.cancel",
        status="succeeded",
        output_type="workflow_cancellation_outcome",
        summary="The workflow had already failed.",
        data={"outcome": "already_failed", "workflow_status": "failed"},
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    response = render_typed_response(
        action="answer",
        content="Choose the pending workflow action shown above.",
        plan=None,
        facts=facts,
        state_version="cancel-shape",
        stop_reason="answered",
        observations=[observation],
    )

    assert "already failed" in response.content
    assert "Workflow status: Failed" in response.content
    assert "already_failed" not in response.content
    assert "Capability result" not in response.content
    assert "pending workflow action" not in response.content


def test_documentation_pack_catalog_exposes_registered_handler_item_shape() -> None:
    observation = CapabilityObservation(
        capability_id="mcp.docs.list_documentation_packs",
        status="succeeded",
        output_type="documentation_pack_catalog",
        summary="Found 1 documentation Pack.",
        data={
            "items": [
                {
                    "kind": "organization",
                    "name": "Security Vulnerability Scanning Pack",
                    "version": "2.0",
                    "description": "Builds evidence-backed security documentation.",
                    "adds": ["Vulnerability Report", "Remediation Plan"],
                }
            ]
        },
    )
    facts = build_response_fact_catalog(
        observations=[observation], conversation_state={}, workflow_state=[]
    )
    values = " ".join(item.value for item in facts)

    assert "Security Vulnerability Scanning Pack" in values
    assert "Vulnerability Report" in values
