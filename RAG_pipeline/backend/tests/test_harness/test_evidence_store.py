from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.db.models import Conversation, User
from src.db.models.harness import HarnessWorkflowRunRow
from src.generation.agent_loop import RuntimeLoopOutcome, RuntimeProgressEvent
from src.harness.evidence import query_runtime_evidence
from src.harness.evidence_store.conflicts import group_conflicts
from src.harness.evidence_store.composer import (
    InvestigationAnswer,
    InvestigationConfidence,
    OperationalAnswerComposer,
)
from src.harness.evidence_store.models import EvidenceSnapshot, EvidenceSourceClass
from src.harness.evidence_store.ranking import EvidenceRankingContext, rank_evidence
from src.harness.evidence_store.repository import HarnessEvidenceRepository
from src.harness.evidence_store.synthesis import (
    EvidenceAnswerStatus,
    EvidenceAnswerSynthesizer,
    StructuredEvidenceAnswer,
    aggregate_runtime_failures,
)
from src.harness.evidence_store.writer import EvidenceGroundedWriter
from src.harness.planner import (
    EvidencePolicy,
    TurnExecutionMode,
    TurnPlan,
    TurnTaskType,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


async def test_runtime_evidence_uses_agent_tool_receipts_and_preserves_scope(
    db_session, monkeypatch
):
    captured = {}

    async def runtime_loop(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return RuntimeLoopOutcome(
            final_answer="ESB_PROD is online and had 12 executions in the requested window.",
            used_runtime_data=True,
            progress_events=[
                RuntimeProgressEvent(
                    "runtime_tool_result",
                    {
                        "tool_name": "list_runtime_engines",
                        "success": True,
                        "data": [
                            {
                                "engine_id": "engine-1",
                                "engine_name": "ESB_PROD",
                                "status": "online",
                                "environment_name": "PROD",
                                "last_seen_at": "2026-07-11T11:00:00Z",
                            }
                        ],
                    },
                )
            ],
        )

    monkeypatch.setattr("src.harness.evidence.run_runtime_tool_loop", runtime_loop)
    result = await query_runtime_evidence(
        question="Show ESB_PROD executions and health",
        project_id="cdc",
        project_scope=["cdc"],
        db=db_session,
        since=NOW - timedelta(days=2),
        until=NOW,
    )

    assert result.status == "succeeded"
    assert captured["project_scope"] == ["cdc"]
    assert "selected project is cdc" in captured["messages"][0]["content"]
    assert captured["messages"][-1]["content"] == "Show ESB_PROD executions and health"
    assert result.items[0].metadata["runtime_answer"].startswith("ESB_PROD is online")
    assert result.items[0].metadata["tools_used"] == ["list_runtime_engines"]
    assert any(item.title == "ESB_PROD" for item in result.items)
    assert result.answer_draft == (
        "ESB_PROD is online and had 12 executions in the requested window."
    )


def test_runtime_draft_preserves_markdown_layout():
    item = evidence(
        source_class=EvidenceSourceClass.CURRENT_RUNTIME,
        source_id="runtime-answer",
        summary="ESB_PROD runtime evidence",
    )
    structured = EvidenceAnswerSynthesizer().compose(
        question="Show ESB_PROD",
        plan=turn_plan(output_contract="runtime_answer", runtime=True),
        evidence=[item],
        draft="## Engines\n\n| Engine | Status |\n| --- | --- |\n| ESB_PROD | online |",
    )

    assert structured.sections[0].body == (
        "## Engines\n\n| Engine | Status |\n| --- | --- |\n| ESB_PROD | online |"
    )


def evidence(source_class=EvidenceSourceClass.INDEXED_DOC, source_id="doc-1", **changes):
    values = {
        "source_class": source_class,
        "source_id": source_id,
        "project_ref": "project-a",
        "title": source_id,
        "summary": f"Summary from {source_id}",
        "observed_at": NOW - timedelta(hours=1),
        "retrieved_at": NOW,
        "freshness": 0.8,
        "confidence": 0.8,
        "authority": 0.7,
    }
    values.update(changes)
    return EvidenceSnapshot(**values)


def turn_plan(*, output_contract="documentation_explanation", runtime=False):
    allowed = ["runtime_evidence"] if runtime else ["indexed_documentation"]
    denied = (
        ["indexed_documentation", "generated_documentation", "connector_evidence"]
        if runtime
        else ["runtime_evidence", "connector_evidence", "generated_documentation"]
    )
    return TurnPlan(
        objective="Answer from evidence",
        task_type=TurnTaskType.RUNTIME_ANSWER if runtime else TurnTaskType.DOCUMENT_ANSWER,
        resolved_scope={"project_id": "project-a"},
        evidence_policy=EvidencePolicy(
            allowed=allowed,
            denied=denied,
            preferred=allowed,
            required=allowed,
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract=output_contract,
        confidence=0.9,
    )


async def create_run(db_session, project_id="project-a"):
    user = User(username=f"user-{project_id}", email=f"{project_id}@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Evidence", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    run = HarnessWorkflowRunRow(
        conversation_id=conversation.id,
        user_id=user.id,
        project_id=project_id,
        intent_mode="investigate",
        capability_id="runtime.tools.query_evidence",
        provider_id="runtime",
        status="running",
        started_at=NOW,
    )
    db_session.add(run)
    await db_session.flush()
    return run


def test_snapshot_sanitizes_sensitive_and_bounded_metadata():
    item = evidence(
        metadata={
            "status": "healthy",
            "api_token": "must disappear",
            "nested": {"password": "must disappear", "note": "x" * 1200},
            "many": list(range(100)),
        }
    )

    assert item.metadata["status"] == "healthy"
    assert "api_token" not in item.metadata
    assert "password" not in item.metadata["nested"]
    assert len(item.metadata["nested"]["note"]) == 1000
    assert len(item.metadata["many"]) == 50

    with pytest.raises(ValidationError, match="8192 bytes"):
        evidence(metadata={f"field-{index}": "x" * 1000 for index in range(20)})


def test_ranking_is_scope_hint_quality_then_stable_identity():
    runtime = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "runtime-z",
        authority=0.6,
        freshness=1.0,
        score=0.9,
    )
    preferred_doc = evidence(authority=0.9, freshness=0.4, score=0.5)
    wrong_project = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "runtime-a",
        project_ref="project-b",
        authority=1.0,
        freshness=1.0,
        score=1.0,
    )
    context = EvidenceRankingContext(
        project_ref="project-a",
        preferred_sources=(EvidenceSourceClass.CURRENT_RUNTIME,),
    )

    ranked = rank_evidence([wrong_project, preferred_doc, runtime], context)

    assert [item.source_id for item in ranked] == ["runtime-z", "doc-1", "runtime-a"]
    assert rank_evidence(list(reversed(ranked)), context) == ranked


def test_conflicts_retain_both_values_with_deterministic_group():
    docs = evidence(
        EvidenceSourceClass.GENERATED_DOC,
        "runbook",
        id=10,
        claim_key="job.payment.status",
        claim_value="Healthy",
    )
    runtime = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "execution-42",
        id=11,
        claim_key=" job.payment.status ",
        claim_value="FAILED",
    )

    grouped = group_conflicts([runtime, docs])

    assert grouped[0].conflict_group == grouped[1].conflict_group
    assert grouped[0].conflict_group.startswith("conflict:")
    assert grouped[0].conflict_refs == [10]
    assert grouped[1].conflict_refs == [11]
    assert group_conflicts([docs, runtime])[0].conflict_group == grouped[1].conflict_group


def test_conflicts_are_scoped_by_entity_environment_and_time_window():
    job_a = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "a",
        id=20,
        entity_ref="job-a",
        claim_key="execution_status",
        claim_value="Failed",
        metadata={"environment": "prod", "time_window": "2026-07-01/2026-07-02"},
    )
    job_b = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "b",
        id=21,
        entity_ref="job-b",
        claim_key="execution_status",
        claim_value="Healthy",
        metadata={"environment": "prod", "time_window": "2026-07-01/2026-07-02"},
    )
    other_environment = job_a.model_copy(
        update={
            "id": 22,
            "source_id": "a-test",
            "claim_value": "Healthy",
            "metadata": {"environment": "test", "time_window": "2026-07-01/2026-07-02"},
        }
    )

    grouped = group_conflicts([job_a, job_b, other_environment])

    assert all(item.conflict_group is None for item in grouped)


async def test_repository_persists_collection_use_order_and_run_scope(db_session):
    first_run = await create_run(db_session)
    second_run = await create_run(db_session, "project-b")
    repo = HarnessEvidenceRepository(db_session)
    collected = await repo.collect(
        first_run.id,
        [evidence(source_id="doc-a"), evidence(EvidenceSourceClass.CURRENT_RUNTIME, "run-a")],
    )
    await repo.collect(second_run.id, [evidence(source_id="doc-b", project_ref="project-b")])

    used = await repo.mark_used(first_run.id, [collected[1].id, collected[0].id])

    assert [item.collected_order for item in await repo.list_collected(first_run.id)] == [1, 2]
    assert [item.source_id for item in used] == ["run-a", "doc-a"]
    assert [item.used_order for item in used] == [1, 2]
    with pytest.raises(ValueError, match="belong"):
        other = (await repo.list_collected(second_run.id))[0]
        await repo.mark_used(first_run.id, [other.id])


def test_writer_distinguishes_docs_runtime_and_cites_only_used():
    collected_only = evidence(id=1, collected_order=1)
    docs = evidence(
        EvidenceSourceClass.GENERATED_DOC,
        "runbook",
        id=2,
        collected_order=2,
        used_order=2,
        conflict_group="conflict:1",
    )
    runtime = evidence(
        EvidenceSourceClass.CURRENT_RUNTIME,
        "execution",
        id=3,
        collected_order=3,
        used_order=1,
    )

    response = EvidenceGroundedWriter().write([collected_only, docs, runtime])

    assert response.text.startswith("Current runtime evidence")
    assert "Generated documentation" in response.text
    assert "Conflicts with another observation" in response.text
    assert [citation.evidence_id for citation in response.citations] == [3, 2]
    assert "[1]" not in response.text


def test_component_inventory_synthesis_is_grounded_and_hides_internal_ids():
    docs = [
        evidence(
            id=31,
            source_id="inventory",
            capability_id="rag.docs.search_evidence",
            title="02_COMPONENT_INVENTORY.md",
            summary="AssetCheck reads Maximo assets and publishes validated assets.",
            metadata={
                "component": "AssetCheck",
                "purpose": "Validate Maximo assets",
                "connections": ["Maximo", "Azure SQL"],
            },
        )
    ]

    answer = EvidenceAnswerSynthesizer().compose(
        question="List all jobs and their connections",
        plan=turn_plan(output_contract="component_inventory"),
        evidence=docs,
        draft=(
            "AssetCheck validates assets before loading them. "
            "The result came from rag.docs.search_evidence."
        ),
    )

    assert answer.answer_status is EvidenceAnswerStatus.COMPLETE
    assert answer.sections[1].rows[0]["component"] == "AssetCheck"
    assert answer.sections[1].rows[0]["connections"] == "Maximo, Azure SQL"
    assert answer.evidence_refs == ["31"]
    assert "rag.docs" not in answer.as_markdown()
    assert "the available source" not in answer.as_markdown()


def test_runtime_failure_ranking_uses_observed_or_explicit_counts_only():
    runtime = [
        evidence(
            EvidenceSourceClass.CURRENT_RUNTIME,
            "run-1",
            id=41,
            entity_ref="job-a",
            summary="Runtime failure: job-a - Execution one failed",
            metadata={"job_name": "job-a", "state": "failed"},
        ),
        evidence(
            EvidenceSourceClass.CURRENT_RUNTIME,
            "run-2",
            id=42,
            entity_ref="job-a",
            summary="Runtime failure: job-a - Execution two failed",
            metadata={"job_name": "job-a", "state": "failed"},
        ),
        evidence(
            EvidenceSourceClass.CURRENT_RUNTIME,
            "aggregate-b",
            id=43,
            entity_ref="job-b",
            summary="job-b failures",
            metadata={"job_name": "job-b", "state": "failed", "failure_count": 7},
        ),
    ]

    rows, limitations = aggregate_runtime_failures(runtime)
    answer = EvidenceAnswerSynthesizer().compose(
        question="Which jobs failed most?",
        plan=turn_plan(output_contract="runtime_ranking", runtime=True),
        evidence=runtime,
        draft="job-b has the highest supported failure count.",
    )

    assert rows == [
        {"rank": 1, "job": "job-b", "failures": 7},
        {"rank": 2, "job": "job-a", "failures": 2},
    ]
    assert limitations
    assert answer.answer_status is EvidenceAnswerStatus.PARTIAL
    assert answer.sections[0].rows == rows
    assert "highest supported" not in answer.as_markdown()


def test_runtime_failure_ranking_rejects_provider_chatter_when_no_counts_exist():
    runtime = [
        evidence(
            EvidenceSourceClass.CURRENT_RUNTIME,
            "empty-observation",
            id=44,
            summary="Runtime provider returned no matching executions.",
        )
    ]

    answer = EvidenceAnswerSynthesizer().compose(
        question="Which jobs failed most in the last 30 days?",
        plan=turn_plan(output_contract="runtime_ranking", runtime=True),
        evidence=runtime,
        draft=(
            "Let me also list the jobs inventory. list_runtime_health returned nothing. "
            "The documentation says there are 10 Talend jobs."
        ),
    )

    rendered = answer.as_markdown()
    assert answer.answer_status is EvidenceAnswerStatus.PARTIAL
    assert answer.sections[0].heading == "Result"
    assert "No countable runtime failure observations" in rendered
    assert "Let me" not in rendered
    assert "list_runtime_health" not in rendered
    assert "10 Talend jobs" not in rendered


def test_empty_runtime_ranking_has_one_scope_specific_limitation():
    answer = EvidenceAnswerSynthesizer().compose(
        question="Which jobs failed most in the last 30 days?",
        plan=turn_plan(output_contract="runtime_ranking", runtime=True),
        evidence=[],
        draft="Let me check another source.",
    )

    assert answer.answer_status is EvidenceAnswerStatus.INSUFFICIENT
    assert answer.limitations == [
        "No runtime failure observations were returned for the requested scope."
    ]


def test_migration_and_incident_contracts_keep_findings_primary():
    docs = [
        evidence(
            id=51,
            title="03_DATA_FLOWS_AND_LINEAGE.md",
            summary="AssetCheck moves Maximo assets into SQL after validation.",
        )
    ]
    proposal = EvidenceAnswerSynthesizer().compose(
        question="Migrate a component to ADF",
        plan=turn_plan(output_contract="migration_proposal"),
        evidence=docs,
        draft=(
            "Use AssetCheck as the first candidate. Map extraction to Copy Activity, "
            "validation to Data Flow, and orchestration to an ADF pipeline."
        ),
    )
    runtime = [
        evidence(
            EvidenceSourceClass.CURRENT_RUNTIME,
            "failed-run",
            id=52,
            entity_ref="AssetCheck",
            summary="AssetCheck failed at 02:10; no root-cause signal was returned.",
            metadata={"job_name": "AssetCheck", "state": "failed"},
        )
    ]
    incident = EvidenceAnswerSynthesizer().compose(
        question="Why did AssetCheck fail?",
        plan=turn_plan(output_contract="incident_report", runtime=True),
        evidence=runtime,
        draft="AssetCheck failed at 02:10. The cause is not established by the available evidence.",
    )

    assert proposal.headline == "ADF migration proposal"
    assert "AssetCheck" in proposal.as_markdown()
    assert incident.headline == "Operational finding"
    assert incident.as_markdown().index("AssetCheck failed") < incident.as_markdown().index("Limitations") if "Limitations" in incident.as_markdown() else True


def test_documentation_failure_guidance_is_not_current_runtime_status():
    docs = evidence(
        summary=(
            "Debug a failed run by checking LogInfo. A child job failure may be silent "
            "when no error catcher is configured."
        )
    )

    finding = OperationalAnswerComposer().compose(
        [docs], missing_required_sources=["runtime evidence"]
    )
    assert finding.direct_status == (
        "The current status could not be established from the available evidence."
    )

    plan = TurnPlan(
        objective="Why did the job fail?",
        task_type=TurnTaskType.OPERATIONAL_INVESTIGATION,
        resolved_scope={"project_id": "project-a"},
        evidence_policy=EvidencePolicy(
            allowed=["indexed_documentation", "runtime_evidence"],
            denied=["connector_evidence", "generated_documentation"],
            preferred=["runtime_evidence", "indexed_documentation"],
            required=["runtime_evidence"],
        ),
        execution_mode=TurnExecutionMode.WORKFLOW,
        output_contract="incident_report",
        confidence=0.9,
    )
    answer = EvidenceAnswerSynthesizer().compose(
        question=plan.objective,
        plan=plan,
        evidence=[docs],
        draft="The documentation explains where to inspect a failed run.",
    )
    assert answer.domain_status == "unknown"


def test_incident_synthesis_uses_typed_result_and_deduplicates_documents():
    docs = [
        evidence(
            id=71,
            source_id="indexed-agent-brief",
            title="00_AGENT_BRIEF.md (Project: alitalend)",
            summary="General recovery guidance.",
        ),
        evidence(
            EvidenceSourceClass.GENERATED_DOC,
            "generated-agent-brief",
            id=72,
            title="00_AGENT_BRIEF.md",
            summary="Generated documentation file.",
            uri="/projects/alitalend/docs/00_AGENT_BRIEF.md",
        ),
        evidence(
            id=73,
            source_id="operations-guide",
            title="06_OPERATIONS_TESTING_AND_DEBUGGING.md (Project: alitalend)",
            summary="General debugging guidance.",
        ),
    ]
    plan = TurnPlan(
        objective="Why did AssetCheck_Main_Job fail last night?",
        task_type=TurnTaskType.OPERATIONAL_INVESTIGATION,
        resolved_scope={"project_id": "alitalend"},
        evidence_policy=EvidencePolicy(
            allowed=[
                "indexed_documentation",
                "generated_documentation",
                "runtime_evidence",
                "connector_evidence",
            ],
            denied=[],
            preferred=["runtime_evidence", "connector_evidence"],
            required=["runtime_evidence"],
        ),
        execution_mode=TurnExecutionMode.WORKFLOW,
        output_contract="incident_report",
        confidence=0.9,
    )
    investigation = InvestigationAnswer(
        direct_status="The current status could not be established from the available evidence.",
        likely_cause="The available evidence does not establish a likely cause.",
        confidence=InvestigationConfidence.LOW,
        confidence_score=0.2,
        qualifications=[
            "A complete investigation requires connector/log evidence, runtime evidence.",
            "No source supplied a supported cause claim.",
        ],
        next_action="Connect or restore runtime evidence and rerun the investigation.",
        missing_required_sources=["connector/log evidence", "runtime evidence"],
    )

    answer = EvidenceAnswerSynthesizer().compose_incident(
        question=plan.objective,
        plan=plan,
        evidence=docs,
        investigation=investigation,
    )

    assert answer.headline == "Cause could not be determined"
    assert answer.answer_status is EvidenceAnswerStatus.INSUFFICIENT
    assert answer.domain_status == "unknown"
    assert [citation.label for citation in answer.citations] == [
        "00_AGENT_BRIEF.md",
        "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
    ]
    rendered = answer.as_markdown()
    assert "Sources:" not in rendered
    assert "Missing required sources:" not in rendered
    assert "00_AGENT_BRIEF.md" not in rendered
    assert sum("Unavailable for this investigation" in item for item in answer.limitations) == 1
    fallback = investigation.as_text()
    assert "Sources:" not in fallback
    assert "Missing required sources:" not in fallback
    assert "Unavailable evidence: connector/log evidence, runtime evidence." in fallback


def test_synthesis_rejects_denied_evidence_and_invalid_citations():
    denied = evidence(EvidenceSourceClass.CURRENT_RUNTIME, "runtime", id=61)
    synthesizer = EvidenceAnswerSynthesizer()
    with pytest.raises(ValueError, match="denied"):
        synthesizer.compose(
            question="Use only docs",
            plan=turn_plan(),
            evidence=[denied],
            draft="Runtime says healthy.",
        )

    docs = [evidence(id=62)]
    valid = synthesizer.compose(
        question="Explain",
        plan=turn_plan(),
        evidence=docs,
        draft="The documentation supports this answer.",
    )
    invalid = StructuredEvidenceAnswer.model_validate(
        {
            **valid.model_dump(),
            "citations": [
                {
                    "evidence_id": "missing",
                    "label": "Missing",
                    "source_class": "indexed_doc",
                }
            ],
            "evidence_refs": ["missing"],
        }
    )
    with pytest.raises(ValueError, match="available evidence"):
        synthesizer.validate(invalid, plan=turn_plan(), evidence=docs)
