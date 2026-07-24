from __future__ import annotations

import datetime

from src.db.models import Conversation, User
from src.harness.models import WorkflowRunCreate, WorkflowRunStatus
from src.harness.providers.mcp_docs import (
    McpDocumentationCancellationResult,
    McpDocumentationGenerationStatus,
    McpDocumentationProviderError,
)
from src.harness.repository import HarnessWorkflowRepository
from src.harness.reconciliation import DocumentationWorkflowReconciler
from src.harness.workflow_control import (
    WorkflowCancellationController,
    WorkflowCancellationOutcome,
)


async def _seed(db_session, *, username: str = "workflow-owner"):
    user = User(username=username, email=f"{username}@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Workflow cancellation", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


async def _run(db_session, user, conversation, *, project_id="project-a", status=WorkflowRunStatus.TRIGGERED):
    return await HarnessWorkflowRepository(db_session).create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id=project_id,
            intent_mode="generate_docs",
            capability_id="mcp.docs.generate_project_documentation",
            provider_id="mcp.docs",
            status=status,
            started_at=datetime.datetime.now(datetime.UTC),
        )
    )


def _status(project_id: str, status: str, *, error: str | None = None):
    return McpDocumentationGenerationStatus(
        project_id=project_id,
        found=True,
        run_id=41,
        status=status,
        started_at="2026-07-18T12:00:00Z",
        completed_at=("2026-07-18T12:00:12Z" if status not in {"running", "pending"} else None),
        doc_count=0,
        quality_score=None,
        run_verdict="failed" if status == "failed" else None,
        client_ready=None,
        failed_gates=[],
        error_message=error,
        status_path=f"/mcp/generate-runs/{project_id}",
        docs_path=f"/projects/{project_id}/docs",
    )


class Provider:
    def __init__(self, statuses, *, cancellation=None):
        self.statuses = list(statuses)
        self.cancellation = cancellation
        self.cancel_calls = 0

    async def get_generation_status(
        self, *, project_id, external_run_id=None, access_token=None
    ):
        del external_run_id
        del access_token
        value = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(value, Exception):
            raise value
        return value

    async def cancel_generation(self, *, project_id, access_token=None):
        del access_token
        self.cancel_calls += 1
        return self.cancellation or McpDocumentationCancellationResult(
            project_id=project_id,
            accepted=True,
            status="cancelled",
            message="Cancellation accepted.",
        )


async def test_orphaned_restart_child_reconciles_before_cancellation(db_session) -> None:
    user, conversation = await _seed(db_session, username="orphan-owner")
    run = await _run(db_session, user, conversation)
    provider = Provider([_status("project-a", "failed", error="orphaned at restart")])

    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=user.id,
        project_scope=["project-a"],
        conversation_id=conversation.id,
        workflow_reference=None,
        access_token="transient",
    )

    assert result.outcome is WorkflowCancellationOutcome.ALREADY_FAILED
    assert result.run is not None and result.run.id == run.id
    assert result.run.status is WorkflowRunStatus.FAILED
    assert result.run.error_message == "orphaned at restart"
    assert provider.cancel_calls == 0


async def test_running_child_is_cancelled_and_parent_becomes_canonical_cancelled(db_session) -> None:
    user, conversation = await _seed(db_session, username="cancel-owner")
    run = await _run(db_session, user, conversation)
    provider = Provider(
        [_status("project-a", "running")],
        cancellation=McpDocumentationCancellationResult(
            project_id="project-a",
            accepted=True,
            status="reset",
            message="Project reset to error.",
        ),
    )

    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=user.id,
        project_scope=["project-a"],
        conversation_id=conversation.id,
        workflow_reference="project-a",
        access_token=None,
    )

    assert result.outcome is WorkflowCancellationOutcome.CANCELLED
    assert result.run is not None and result.run.id == run.id
    assert result.run.status is WorkflowRunStatus.CANCELLED
    assert result.run.cancelled_at is not None
    assert result.run.finished_at is not None
    assert result.run.runtime_metadata["external_generation"]["status"] == "cancelled"
    assert len(result.run.children) == 1
    assert result.run.children[0].status == "running"
    assert result.run.children[0].cancellation_state == "accepted"
    assert result.run.children[0].cancellation_provider_status == "reset"
    assert provider.cancel_calls == 1

    # MCP's current reset contract later materializes an external failed fact.
    # The typed child records it while terminal precedence preserves the local
    # accepted user cancellation on the parent.
    reconciled = await DocumentationWorkflowReconciler(
        HarnessWorkflowRepository(db_session),
        Provider([_status("project-a", "failed", error="Generation cancelled by user")]),
    ).reconcile(result.run, access_token=None, force=True)
    assert reconciled.run.status is WorkflowRunStatus.CANCELLED
    assert reconciled.run.children[0].status == "failed"
    assert reconciled.run.children[0].cancellation_state == "accepted"


async def test_authoritative_cancelled_child_reports_already_cancelled(db_session) -> None:
    user, conversation = await _seed(db_session, username="already-cancelled-owner")
    await _run(db_session, user, conversation)
    provider = Provider([_status("project-a", "cancelled")])

    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=user.id,
        project_scope=["project-a"],
        conversation_id=conversation.id,
        workflow_reference=None,
        access_token=None,
    )

    assert result.outcome is WorkflowCancellationOutcome.ALREADY_CANCELLED
    assert result.run is not None
    assert result.run.status is WorkflowRunStatus.CANCELLED
    assert provider.cancel_calls == 0


async def test_provider_unavailable_never_claims_cancellation(db_session) -> None:
    user, conversation = await _seed(db_session, username="provider-down-owner")
    await _run(db_session, user, conversation)
    provider = Provider([McpDocumentationProviderError("offline")])

    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=user.id,
        project_scope=["project-a"],
        conversation_id=conversation.id,
        workflow_reference=None,
        access_token=None,
    )

    assert result.outcome is WorkflowCancellationOutcome.PROVIDER_UNAVAILABLE
    assert result.run is not None
    assert result.run.status is WorkflowRunStatus.TRIGGERED
    assert provider.cancel_calls == 0


async def test_reference_ambiguity_and_no_match_are_distinct(db_session) -> None:
    user, conversation = await _seed(db_session, username="ambiguous-owner")
    await _run(db_session, user, conversation, project_id="project-a")
    await _run(db_session, user, conversation, project_id="project-b")
    provider = Provider([_status("project-a", "running")])
    controller = WorkflowCancellationController(HarnessWorkflowRepository(db_session), provider)

    ambiguous = await controller.cancel(
        owner_user_id=user.id,
        project_scope=["project-a", "project-b"],
        conversation_id=conversation.id,
        workflow_reference="documentation generation",
        access_token=None,
    )
    missing = await controller.cancel(
        owner_user_id=user.id,
        project_scope=["project-a", "project-b"],
        conversation_id=conversation.id,
        workflow_reference="unknown-project",
        access_token=None,
    )

    assert ambiguous.outcome is WorkflowCancellationOutcome.AMBIGUOUS
    assert len(ambiguous.options) == 2
    assert missing.outcome is WorkflowCancellationOutcome.NO_MATCH
    assert provider.cancel_calls == 0


async def test_project_scope_and_owner_both_isolate_cancellation(db_session) -> None:
    owner, conversation = await _seed(db_session, username="private-owner")
    caller = User(username="other-caller", email="other-caller@example.com")
    db_session.add(caller)
    await db_session.flush()
    await _run(db_session, owner, conversation, project_id="private-project")
    provider = Provider([_status("private-project", "running")])

    wrong_owner = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=caller.id,
        project_scope=["private-project"],
        conversation_id=conversation.id,
        workflow_reference=None,
        access_token=None,
    )
    out_of_scope = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=owner.id,
        project_scope=[],
        conversation_id=conversation.id,
        workflow_reference=None,
        access_token=None,
    )

    assert wrong_owner.outcome is WorkflowCancellationOutcome.NO_MATCH
    assert out_of_scope.outcome is WorkflowCancellationOutcome.NO_MATCH
    assert provider.cancel_calls == 0


async def test_expected_version_conflict_prevents_provider_side_effect(db_session) -> None:
    user, conversation = await _seed(db_session, username="race-owner")
    run = await _run(db_session, user, conversation)
    provider = Provider([_status("project-a", "running")])

    result = await WorkflowCancellationController(
        HarnessWorkflowRepository(db_session), provider
    ).cancel(
        owner_user_id=user.id,
        project_scope=["project-a"],
        conversation_id=None,
        workflow_reference=str(run.id),
        access_token=None,
        expected_version=run.version + 1,
    )

    assert result.outcome is WorkflowCancellationOutcome.CONFLICT
    assert provider.cancel_calls == 0
