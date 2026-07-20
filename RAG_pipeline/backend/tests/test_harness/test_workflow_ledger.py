import datetime

import pytest

from src.db.models import Conversation, User
from src.harness import HarnessRunNotFoundError
from src.harness.models import WorkflowRunCreate, WorkflowRunStatus, WorkflowRunUpdate
from src.harness.reconciliation import (
    DocumentationWorkflowCallbackIngestor,
    DocumentationWorkflowReconciler,
)
from src.harness.reconciliation import DocumentationWorkflowReconciliationScheduler
from src.harness.providers.mcp_docs import (
    McpDocumentationGenerationStatus,
    McpDocumentationProviderError,
)
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository
from src.harness.workflow_ledger import (
    IllegalWorkflowTransition,
    WorkflowChildStatus,
    WorkflowObservationSource,
    decide_child_observation,
    validate_parent_transition,
)


async def _seed_run(db_session, *, project_id: str = "project-a"):
    user = User(
        username=f"ledger-{project_id}",
        email=f"ledger-{project_id}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Ledger", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    run = await HarnessWorkflowRepository(db_session).create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id=project_id,
            intent_mode="generate_docs",
            capability_id="mcp.docs.generate_project_documentation",
            provider_id="mcp.docs",
            status=WorkflowRunStatus.TRIGGERED,
        )
    )
    return user, run


def test_parent_transition_allowlist_covers_legitimate_lifecycle_and_rejects_reopen() -> None:
    legitimate = (
        (WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING),
        (WorkflowRunStatus.RUNNING, WorkflowRunStatus.INTERRUPTED),
        (WorkflowRunStatus.INTERRUPTED, WorkflowRunStatus.PENDING),
        (WorkflowRunStatus.PENDING, WorkflowRunStatus.TRIGGERED),
        (WorkflowRunStatus.TRIGGERED, WorkflowRunStatus.INDETERMINATE),
        (WorkflowRunStatus.INDETERMINATE, WorkflowRunStatus.SUCCEEDED),
        (WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.SUCCEEDED),
    )
    for current, target in legitimate:
        validate_parent_transition(current, target)
    with pytest.raises(IllegalWorkflowTransition):
        validate_parent_transition(WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.RUNNING)
    with pytest.raises(IllegalWorkflowTransition):
        validate_parent_transition(WorkflowRunStatus.TRIGGERED, WorkflowRunStatus.PENDING)


async def test_exact_child_binding_is_idempotent_unique_and_scope_bound(db_session) -> None:
    user, run = await _seed_run(db_session)
    repo = HarnessWorkflowRepository(db_session)
    first = await repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id=41,
        project_id="project-a",
        provider_status="running",
        owner_user_id=user.id,
    )
    duplicate = await repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id="41",
        project_id="project-a",
        provider_status="running",
        owner_user_id=user.id,
    )
    assert duplicate.id == first.id
    with pytest.raises(HarnessWorkflowConflictError):
        await repo.bind_child_receipt(
            ["project-a"],
            workflow_run_id=run.id,
            provider_id="mcp.docs",
            child_kind="documentation_generation",
            external_run_id=42,
            project_id="project-a",
            provider_status="running",
            owner_user_id=user.id,
        )
    with pytest.raises(HarnessRunNotFoundError, match="Unknown workflow_run_id"):
        await repo.bind_child_receipt(
            ["other-project"],
            workflow_run_id=run.id,
            provider_id="other",
            child_kind="documentation_generation",
            external_run_id=99,
            project_id="project-a",
            provider_status="running",
            owner_user_id=user.id,
        )
    assert await repo.list_runs(
        ["other-project"], owner_user_id=user.id
    ) == []


async def test_poll_observations_are_duplicate_safe_and_reject_stale_terminal_rewrites(
    db_session,
) -> None:
    user, run = await _seed_run(db_session)
    repo = HarnessWorkflowRepository(db_session)
    t1 = datetime.datetime(2026, 7, 18, 10, tzinfo=datetime.UTC)
    t2 = t1 + datetime.timedelta(minutes=1)
    receipt = await repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id=51,
        project_id="project-a",
        provider_status="running",
        provider_timestamp=t1,
        owner_user_id=user.id,
    )
    completed, applied = await repo.record_child_observation(
        child_receipt_id=receipt.id,
        idempotency_key="poll:51:completed:t2",
        source=WorkflowObservationSource.POLL,
        provider_status="completed",
        provider_timestamp=t2,
    )
    assert applied is True
    assert completed.status == WorkflowChildStatus.SUCCEEDED.value
    duplicate, applied = await repo.record_child_observation(
        child_receipt_id=receipt.id,
        idempotency_key="poll:51:completed:t2",
        source=WorkflowObservationSource.POLL,
        provider_status="completed",
        provider_timestamp=t2,
    )
    assert applied is False
    assert duplicate.version == completed.version
    stale, applied = await repo.record_child_observation(
        child_receipt_id=receipt.id,
        idempotency_key="poll:51:failed:t1",
        source=WorkflowObservationSource.POLL,
        provider_status="failed",
        provider_timestamp=t1,
    )
    assert applied is False
    assert stale.status == WorkflowChildStatus.SUCCEEDED.value


async def test_callback_seam_binds_exact_child_and_rejects_duplicate_and_stale_facts(
    db_session,
) -> None:
    user, first_run = await _seed_run(db_session, project_id="project-a")
    repo = HarnessWorkflowRepository(db_session)
    second_run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=first_run.conversation_id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id="mcp.docs.generate_project_documentation",
            provider_id="mcp.docs",
            status=WorkflowRunStatus.TRIGGERED,
        )
    )
    t1 = datetime.datetime(2026, 7, 18, 10, tzinfo=datetime.UTC)
    t2 = t1 + datetime.timedelta(minutes=1)
    for run, external_id in ((first_run, 61), (second_run, 62)):
        await repo.bind_child_receipt(
            ["project-a"],
            workflow_run_id=run.id,
            provider_id="mcp.docs",
            child_kind="documentation_generation",
            external_run_id=external_id,
            project_id="project-a",
            provider_status="running",
            provider_timestamp=t1,
            owner_user_id=user.id,
        )
    ingestor = DocumentationWorkflowCallbackIngestor(repo)
    first = await ingestor.ingest(
        provider_id="mcp.docs",
        external_run_id=61,
        idempotency_key="callback:event-1",
        provider_status="completed",
        provider_timestamp=t2,
    )
    assert first.applied is True
    assert first.run.id == first_run.id
    assert first.run.status is WorkflowRunStatus.SUCCEEDED
    assert (await repo.get_run(["project-a"], second_run.id)).status is WorkflowRunStatus.TRIGGERED

    duplicate = await ingestor.ingest(
        provider_id="mcp.docs",
        external_run_id=61,
        idempotency_key="callback:event-1",
        provider_status="completed",
        provider_timestamp=t2,
    )
    assert duplicate.applied is False
    stale = await ingestor.ingest(
        provider_id="mcp.docs",
        external_run_id=61,
        idempotency_key="callback:event-stale",
        provider_status="failed",
        provider_timestamp=t1,
        error_message="Authorization: Bearer super-secret-token",
    )
    assert stale.applied is False
    assert stale.run.status is WorkflowRunStatus.SUCCEEDED
    child = stale.run.children[0]
    assert child.status == WorkflowChildStatus.SUCCEEDED.value
    assert "super-secret-token" not in (child.error_message or "")


def test_child_observation_policy_preserves_terminal_precedence_without_timestamps() -> None:
    decision = decide_child_observation(
        current_status=WorkflowChildStatus.CANCELLED,
        current_provider_timestamp=None,
        observed_status=WorkflowChildStatus.FAILED,
        observed_provider_timestamp=None,
    )
    assert decision.accepted is False
    assert decision.status is WorkflowChildStatus.CANCELLED


async def test_repository_enforces_terminal_parent_precedence(db_session) -> None:
    _, run = await _seed_run(db_session)
    repo = HarnessWorkflowRepository(db_session)
    succeeded = await repo.update_run(
        ["project-a"],
        run.id,
        WorkflowRunUpdate(status=WorkflowRunStatus.SUCCEEDED),
    )
    with pytest.raises(IllegalWorkflowTransition):
        await repo.update_run(
            ["project-a"],
            succeeded.id,
            WorkflowRunUpdate(status=WorkflowRunStatus.RUNNING),
        )


async def test_restart_recovery_polls_the_persisted_exact_child(engine) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        _, run = await _seed_run(session)
        repo = HarnessWorkflowRepository(session)
        await repo.bind_child_receipt(
            ["project-a"],
            workflow_run_id=run.id,
            provider_id="mcp.docs",
            child_kind="documentation_generation",
            external_run_id=71,
            project_id="project-a",
            provider_status="running",
        )
        await session.commit()

    seen: list[dict] = []

    class Provider:
        async def get_generation_status(self, **kwargs):  # noqa: ANN003
            seen.append(kwargs)
            return McpDocumentationGenerationStatus(
                project_id="project-a",
                found=True,
                run_id=71,
                status="completed",
                started_at="2026-07-18T10:00:00Z",
                completed_at="2026-07-18T10:01:00Z",
                doc_count=1,
                quality_score=None,
                run_verdict="passed",
                client_ready=True,
                failed_gates=[],
                error_message=None,
                status_path="/mcp/generate-runs/project-a",
                docs_path="/projects/project-a/docs",
            )

    cycle = await DocumentationWorkflowReconciliationScheduler(
        session_factory=session_factory,
        service_access_token="service-secret-not-persisted",
        provider_factory=Provider,
    ).run_once()
    assert cycle.scanned == 1
    assert seen == [
        {
            "project_id": "project-a",
            "access_token": "service-secret-not-persisted",
            "external_run_id": "71",
        }
    ]
    async with session_factory() as session:
        canonical = await HarnessWorkflowRepository(session).get_run(None, run.id)
    assert canonical.status is WorkflowRunStatus.SUCCEEDED
    assert canonical.children[0].external_run_id == "71"
    assert "service-secret-not-persisted" not in str(canonical.model_dump())


async def test_provider_unavailable_preserves_exact_child_and_parent_truth(db_session) -> None:
    user, run = await _seed_run(db_session)
    repo = HarnessWorkflowRepository(db_session)
    child = await repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id=91,
        project_id="project-a",
        provider_status="running",
        owner_user_id=user.id,
    )

    class OfflineProvider:
        async def get_generation_status(self, **_kwargs):
            raise McpDocumentationProviderError("Bearer secret-must-not-persist")

    result = await DocumentationWorkflowReconciler(repo, OfflineProvider()).reconcile(
        await repo.get_run(["project-a"], run.id, owner_user_id=user.id),
        access_token="transient-only",
        force=True,
    )
    assert result.run.status is WorkflowRunStatus.TRIGGERED
    assert result.run.children[0].id == child.id
    assert result.run.children[0].status == "running"
    assert result.run.runtime_metadata["reconciliation"]["last_reason"] == "provider_unavailable"
    assert "secret-must-not-persist" not in str(result.run.model_dump())
    assert "transient-only" not in str(result.run.model_dump())
