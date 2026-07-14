import datetime

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Conversation, User
from src.db.models.harness import HarnessWorkflowCheckpointBlobRow, HarnessWorkflowCheckpointRow
from src.harness.checkpoints import DurableWorkflowCheckpointSaver
from src.harness.models import WorkflowRunCreate, WorkflowRunStatus
from src.harness.providers.mcp_docs import McpDocumentationGenerationStatus
from src.harness.reconciliation import (
    DocumentationWorkflowReconciler,
    DocumentationWorkflowReconciliationScheduler,
)
from src.harness.repository import HarnessWorkflowRepository


async def test_durable_checkpointer_resumes_after_provider_recreation_and_redacts_state(engine) -> None:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    saver = DurableWorkflowCheckpointSaver(session_factory=session_factory)

    def await_scope(state: dict):
        choice = interrupt({"kind": "clarification", "prompt": "Choose a project"})
        return {"project_id": choice["project_id"], "status": "ready"}

    builder = StateGraph(dict)
    builder.add_node("await_scope", await_scope)
    builder.add_edge(START, "await_scope")
    builder.add_edge("await_scope", END)
    config = {"configurable": {"thread_id": "harness-run-77"}}

    first_graph = builder.compile(checkpointer=saver)
    first = await first_graph.ainvoke({"message": "Bearer secret-value", "project_id": None}, config)
    assert first["__interrupt__"]

    async with session_factory() as session:
        persisted = (await session.execute(select(HarnessWorkflowCheckpointRow))).scalars().all()
        assert persisted
        blobs = (await session.execute(select(HarnessWorkflowCheckpointBlobRow))).scalars().all()
        assert b"secret-value" not in b"".join(row.checkpoint_data for row in persisted)
        assert b"secret-value" not in b"".join(row.value_data for row in blobs)
    # A fresh provider/checkpointer instance reads the same durable thread.
    second_graph = builder.compile(checkpointer=DurableWorkflowCheckpointSaver(session_factory=session_factory))
    resumed = await second_graph.ainvoke(Command(resume={"project_id": "project-a"}), config)
    assert resumed["project_id"] == "project-a"
    assert resumed["status"] == "ready"


async def test_reconciliation_transitions_only_from_authoritative_child_status(db_session) -> None:
    user = User(username="reconcile-user", email="reconcile@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Reconcile", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    repo = HarnessWorkflowRepository(db_session)
    run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id="mcp.docs.generate_project_documentation",
            provider_id="mcp.docs",
            status=WorkflowRunStatus.TRIGGERED,
        )
    )

    class Provider:
        async def get_generation_status(self, **kwargs):  # noqa: ANN003
            return McpDocumentationGenerationStatus(
                project_id="project-a",
                found=True,
                run_id=42,
                status="completed",
                started_at=None,
                completed_at="2026-07-14T12:00:00Z",
                doc_count=8,
                quality_score=None,
                run_verdict="passed",
                client_ready=True,
                failed_gates=[],
                error_message=None,
                status_path="/mcp/generate-runs/project-a",
                docs_path="/projects/project-a/docs",
            )

    result = await DocumentationWorkflowReconciler(repo, Provider()).reconcile(
        run, access_token="transient-only"
    )

    assert result.attempted is True
    assert result.authoritative_status == "completed"
    assert result.run.status == WorkflowRunStatus.SUCCEEDED
    assert result.run.runtime_metadata["external_generation"] == {
        "run_id": 42,
        "status": "completed",
        "reconciled_at": result.run.runtime_metadata["external_generation"]["reconciled_at"],
    }
    assert result.run.runtime_metadata["reconciliation"]["state"] == "resolved"


async def test_reconciliation_backs_off_then_marks_indeterminate_without_replaying_provider(db_session) -> None:
    user = User(username="retry-user", email="retry@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Retry", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    repo = HarnessWorkflowRepository(db_session)
    run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id="mcp.docs.generate_project_documentation",
            provider_id="mcp.docs",
            status=WorkflowRunStatus.TRIGGERED,
        )
    )
    now = datetime.datetime(2026, 7, 14, tzinfo=datetime.UTC)
    calls = 0

    class Provider:
        async def get_generation_status(self, **kwargs):  # noqa: ANN003
            nonlocal calls
            calls += 1
            return McpDocumentationGenerationStatus(
                project_id="project-a", found=False, run_id=None, status="not_started",
                started_at=None, completed_at=None, doc_count=0, quality_score=None,
                run_verdict=None, client_ready=None, failed_gates=[], error_message=None,
                status_path="/mcp/generate-runs/project-a", docs_path="/projects/project-a/docs",
            )

    reconciler = DocumentationWorkflowReconciler(repo, Provider(), now=lambda: now, max_attempts=2)
    first = await reconciler.reconcile(run, access_token=None)
    assert first.run.status == WorkflowRunStatus.TRIGGERED
    assert first.run.runtime_metadata["reconciliation"]["attempt_count"] == 1
    skipped = await reconciler.reconcile(first.run, access_token=None)
    assert skipped.attempted is False
    assert calls == 1
    second = await reconciler.reconcile(first.run, access_token=None, force=True)
    assert second.run.status == WorkflowRunStatus.INDETERMINATE
    assert second.run.runtime_metadata["reconciliation"]["state"] == "indeterminate"
    assert calls == 2


async def test_background_reconciliation_uses_service_credential_and_persists_result(engine) -> None:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(username="scheduler-user", email="scheduler@example.com")
        session.add(user)
        await session.flush()
        conversation = Conversation(title="Scheduler", user_id=user.id)
        session.add(conversation)
        await session.flush()
        repo = HarnessWorkflowRepository(session)
        accepted = await repo.create_run(
            WorkflowRunCreate(
                conversation_id=conversation.id,
                user_id=user.id,
                project_id="project-a",
                intent_mode="generate_docs",
                capability_id="mcp.docs.generate_project_documentation",
                provider_id="mcp.docs",
                status=WorkflowRunStatus.TRIGGERED,
            )
        )
        ignored = await repo.create_run(
            WorkflowRunCreate(
                conversation_id=conversation.id,
                user_id=user.id,
                project_id="project-b",
                intent_mode="other",
                capability_id="mcp.docs.get_generation_status",
                provider_id="mcp.docs",
                status=WorkflowRunStatus.TRIGGERED,
            )
        )
        await session.commit()

    seen_tokens: list[str | None] = []

    class Provider:
        async def get_generation_status(self, **kwargs):  # noqa: ANN003
            seen_tokens.append(kwargs["access_token"])
            return McpDocumentationGenerationStatus(
                project_id="project-a",
                found=True,
                run_id=99,
                status="completed",
                started_at=None,
                completed_at="2026-07-14T12:00:00Z",
                doc_count=4,
                quality_score=None,
                run_verdict="passed",
                client_ready=True,
                failed_gates=[],
                error_message=None,
                status_path="/mcp/generate-runs/project-a",
                docs_path="/projects/project-a/docs",
            )

    scheduler = DocumentationWorkflowReconciliationScheduler(
        session_factory=session_factory,
        service_access_token="service-token-only-in-memory",
        provider_factory=Provider,
    )
    result = await scheduler.run_once()

    assert result.scanned == 1
    assert result.attempted == 1
    assert seen_tokens == ["service-token-only-in-memory"]
    async with session_factory() as session:
        repo = HarnessWorkflowRepository(session)
        reconciled = await repo.get_run(None, accepted.id)
        untouched = await repo.get_run(None, ignored.id)
    assert reconciled.status == WorkflowRunStatus.SUCCEEDED
    assert reconciled.runtime_metadata["external_generation"]["run_id"] == 99
    assert "service-token-only-in-memory" not in str(reconciled.runtime_metadata)
    assert untouched.status == WorkflowRunStatus.TRIGGERED
