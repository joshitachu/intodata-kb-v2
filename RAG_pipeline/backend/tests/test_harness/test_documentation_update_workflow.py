from __future__ import annotations

from src.harness.executor import CapabilityExecutionContext, CapabilityObservation
from src.harness.models import DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY
from src.harness.workflows.documentation_update import (
    DocumentationUpdateRequest,
    DocumentationUpdateWorkflow,
)


def _request() -> DocumentationUpdateRequest:
    return DocumentationUpdateRequest(
        workflow_run_id=12,
        project_id="project-a",
        target_doc="04_RUNBOOK.md",
        section="Recovery",
        evidence_refs=["upload:42"],
        evidence_text="Use the new recovery procedure.",
        expected_base_sha="sha256:" + "a" * 64,
    )


async def test_update_workflow_proposes_then_publishes_after_approval() -> None:
    calls = []

    class Executor:
        async def execute(self, request, context):  # noqa: ANN001
            calls.append((request, context))
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="succeeded",
                summary="Updated and indexed 04_RUNBOOK.md.",
                data={
                    "before_sha": request.arguments["expected_base_sha"],
                    "after_sha": "sha256:" + "b" * 64,
                    "reconciliation_status": "indexed",
                },
                artifact_links=[
                    {"label": "Revert Update", "url": "/revert", "kind": "revert"}
                ],
            )

    workflow = DocumentationUpdateWorkflow()
    context = CapabilityExecutionContext(user_id=1, project_scope=["project-a"])
    interrupted = await workflow.run(_request(), executor=Executor(), context=context)

    assert interrupted.status == "interrupted"
    assert interrupted.current_node == "approve"
    assert calls == []
    assert "documentation_update.proposal_created" in {
        item["event_type"] for item in interrupted.activity
    }

    completed = await workflow.resume(
        _request(), approved=True, executor=Executor(), context=context
    )

    assert completed.status == "succeeded"
    assert completed.observation is not None
    assert len(calls) == 1
    request, execution_context = calls[0]
    assert request.capability_id == DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id
    assert request.arguments["expected_base_sha"] == "sha256:" + "a" * 64
    assert DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id in execution_context.approvals


async def test_update_workflow_decline_never_calls_executor() -> None:
    class Executor:
        async def execute(self, request, context):  # noqa: ANN001
            raise AssertionError("declined update must not execute")

    workflow = DocumentationUpdateWorkflow()
    context = CapabilityExecutionContext(user_id=1, project_scope=["project-a"])
    await workflow.run(_request(), executor=Executor(), context=context)

    result = await workflow.resume(
        _request(), approved=False, executor=Executor(), context=context
    )

    assert result.status == "cancelled"
    assert result.observation is None
