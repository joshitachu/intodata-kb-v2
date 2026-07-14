from __future__ import annotations

import json

from src.harness.evidence_store.composer import OperationalAnswerComposer
from src.harness.evidence_store.models import EvidenceSnapshot, EvidenceSourceClass
from src.harness.executor import CapabilityExecutionContext, CapabilityObservation
from src.harness.workflows.operational_investigation import (
    InvestigationCall,
    OperationalInvestigationRequest,
    OperationalInvestigationWorkflow,
)


class FixtureExecutor:
    def __init__(self, observations: dict[str, CapabilityObservation]) -> None:
        self.observations = observations
        self.requests = []

    async def execute(self, request, context):
        self.requests.append((request, context))
        return self.observations[request.capability_id]


def _observation(capability_id: str, *items: dict) -> CapabilityObservation:
    return CapabilityObservation(
        capability_id=capability_id,
        status="succeeded",
        summary=f"Retrieved {len(items)} item(s).",
        data={"items": list(items)},
        safe_details={"evidence_count": len(items)},
    )


def _item(source_id: str, title: str, summary: str, **changes) -> dict:
    values = {
        "id": source_id,
        "title": title,
        "summary": summary,
        "freshness": 0.9,
        "confidence": 0.9,
        "authority": 0.9,
    }
    values.update(changes)
    return values


def _request(*calls: InvestigationCall) -> OperationalInvestigationRequest:
    return OperationalInvestigationRequest(
        workflow_run_id=41,
        question="Why did the payment job fail last night?",
        project_ref="payments",
        entity_refs=["job:payment"],
        calls=list(calls),
    )


def _call(capability_id: str, source_class: EvidenceSourceClass) -> InvestigationCall:
    return InvestigationCall(capability_id=capability_id, source_class=source_class)


async def test_three_source_investigation_succeeds_with_structured_answer():
    executor = FixtureExecutor(
        {
            "docs.search": _observation(
                "docs.search",
                _item("runbook", "Payments runbook", "Retries follow database timeouts."),
            ),
            "runtime.query": _observation(
                "runtime.query",
                _item(
                    "execution-41",
                    "Payment execution",
                    "The execution failed after its database connection timed out.",
                    claim_key="job.payment.current_status",
                    claim_value="Failed",
                ),
                _item(
                    "incident-41",
                    "Payment incident",
                    "The database connection pool was exhausted.",
                    claim_key="likely_cause",
                    claim_value="Database connection pool exhaustion",
                ),
            ),
            "logs.search": _observation(
                "logs.search",
                _item("log-window", "Log window", "Timeouts began at 02:14 UTC."),
            ),
        }
    )

    result = await OperationalInvestigationWorkflow().run(
        _request(
            _call("docs.search", EvidenceSourceClass.INDEXED_DOC),
            _call("runtime.query", EvidenceSourceClass.CURRENT_RUNTIME),
            _call("logs.search", EvidenceSourceClass.CONNECTOR),
        ),
        executor=executor,
        context=CapabilityExecutionContext(user_id=7, project_scope=["payments"]),
    )

    assert result.status == "succeeded"
    assert result.current_node == "finalize"
    assert result.answer.direct_status == "Failed"
    assert result.answer.likely_cause == "Database connection pool exhaustion"
    assert result.answer.confidence.value == "high"
    assert result.answer.missing_required_sources == []
    assert result.sources_touched == [
        "connector/log evidence",
        "documentation",
        "runtime evidence",
    ]
    assert [event["runtime_node"] for event in result.activity] == [
        "resolve",
        "collect",
        "rank",
        "sufficiency",
        "synthesize",
        "finalize",
    ]
    assert all(request.arguments["project_id"] == "payments" for request, _ in executor.requests)


async def test_missing_connector_log_is_insufficient_and_actionable():
    executor = FixtureExecutor(
        {
            "docs.search": _observation(
                "docs.search", _item("runbook", "Runbook", "Database timeout guidance.")
            ),
            "runtime.query": _observation(
                "runtime.query",
                _item(
                    "execution",
                    "Execution",
                    "Failed during database access.",
                    claim_key="current_status",
                    claim_value="Failed",
                ),
                _item(
                    "incident",
                    "Incident",
                    "Connection acquisition timed out.",
                    claim_key="likely_cause",
                    claim_value="Connection acquisition timeout",
                ),
            ),
        }
    )

    result = await OperationalInvestigationWorkflow().run(
        _request(
            _call("docs.search", EvidenceSourceClass.INDEXED_DOC),
            _call("runtime.query", EvidenceSourceClass.CURRENT_RUNTIME),
        ),
        executor=executor,
        context=CapabilityExecutionContext(user_id=7),
    )

    assert result.status == "insufficient"
    assert result.answer.missing_required_sources == ["connector/log evidence"]
    assert "connector/log evidence" in result.answer.next_action
    assert result.answer.confidence.value == "low"
    assert result.answer.answered is False


async def test_runtime_call_normalizes_query_to_question() -> None:
    executor = FixtureExecutor(
        {
            "runtime.query": _observation(
                "runtime.query",
                _item(
                    "execution",
                    "Execution",
                    "The execution failed.",
                    claim_key="execution_status",
                    claim_value="Failed",
                ),
            )
        }
    )
    result = await OperationalInvestigationWorkflow().run(
        OperationalInvestigationRequest(
            workflow_run_id=41,
            question="What failed?",
            project_ref="payments",
            calls=[
                InvestigationCall(
                    capability_id="runtime.query",
                    source_class=EvidenceSourceClass.CURRENT_RUNTIME,
                    arguments={"query": "planner-shaped query"},
                )
            ],
            additional_required_sources=["Slack connector"],
        ),
        executor=executor,
        context=CapabilityExecutionContext(user_id=7),
    )

    arguments = executor.requests[0][0].arguments
    assert arguments["question"] == "planner-shaped query"
    assert "query" not in arguments
    assert set(result.answer.missing_required_sources) == {
        "documentation",
        "Slack connector",
    }


def test_failure_status_outranks_sync_success_and_sources_are_compact() -> None:
    evidence = [
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.CURRENT_RUNTIME,
            source_id="runtime-failure",
            title="Runtime failure",
            summary="Connection to source database timed out (ORA-12170 after 3 attempts)",
            claim_key="likely_cause",
            claim_value="Connection to source database timed out",
            confidence=0.9,
            authority=0.9,
            freshness=0.9,
        ),
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.CURRENT_RUNTIME,
            source_id="failure-rate",
            title="Recent executions",
            summary="Failure rate 0.5, failure streak 1, last failure 2026-07-03.",
            claim_key="schedule",
            claim_value="20:39",
            confidence=0.9,
            authority=0.9,
            freshness=0.9,
        ),
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.SOURCE_TOPOLOGY,
            source_id="sync",
            title="Source sync",
            summary="Sync status is success. " + "raw detail " * 100,
            confidence=0.8,
            authority=0.8,
            freshness=0.8,
        ),
    ]

    answer = OperationalAnswerComposer().compose(evidence)

    assert answer.direct_status == "Failed"
    assert "timed out" in answer.likely_cause
    assert all(len(source.summary) <= 120 for source in answer.source_summary)
    assert len(answer.as_text()) < 1200


def test_composer_discloses_conflicts_and_does_not_choose_a_cause():
    evidence = [
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.CURRENT_RUNTIME,
            source_id="runtime",
            title="Runtime incident",
            summary="Runtime classified a database timeout.",
            claim_key="likely_cause",
            claim_value="Database timeout",
            conflict_group="conflict:one",
            confidence=0.9,
            authority=0.9,
            freshness=0.9,
        ),
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.CONNECTOR,
            source_id="logs",
            title="Log search",
            summary="Logs classified an authentication failure.",
            claim_key="likely_cause",
            claim_value="Authentication failure",
            conflict_group="conflict:one",
            confidence=0.9,
            authority=0.9,
            freshness=0.9,
        ),
    ]

    answer = OperationalAnswerComposer().compose(evidence)

    assert answer.likely_cause == "The available evidence does not establish a likely cause."
    assert answer.confidence.value == "low"
    assert any("conflict" in item.lower() for item in answer.qualifications)
    assert "most recent authoritative source" in answer.next_action
    assert answer.answered is False


def test_safe_output_excludes_payloads_tool_ids_source_ids_and_secrets():
    tool_id = "runtime.tools.raw_log_query"
    evidence = [
        EvidenceSnapshot(
            source_class=EvidenceSourceClass.CONNECTOR,
            source_id="provider-record-991",
            capability_id=tool_id,
            title=f"Result from {tool_id}",
            summary='{"token":"do-not-emit","payload":{"line":"raw"}}',
            uri=f"/internal/{tool_id}/provider-record-991",
            claim_key="current_status",
            claim_value=f"Failed; token=top-secret; checked by {tool_id}",
            metadata={"raw_payload": {"line": "private"}, "tool_id": tool_id},
        )
    ]

    answer = OperationalAnswerComposer().compose(evidence)
    serialized = json.dumps(answer.model_dump(mode="json"), sort_keys=True)

    assert "runtime.tools.raw_log_query" not in serialized
    assert "provider-record-991" not in serialized
    assert "do-not-emit" not in serialized
    assert "top-secret" not in serialized
    assert '"raw_payload"' not in serialized
    assert "Structured source details were omitted." in serialized
    assert answer.source_summary[0].uri is None
