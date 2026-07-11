import datetime
from datetime import UTC

import pytest

from src.db.models import Conversation, User
from src.harness import (
    CapabilityDisabledError,
    CapabilityRegistry,
    HarnessRunNotFoundError,
    HarnessWorkflowRepository,
    HarnessWorkflowService,
    UnknownCapabilityError,
    default_capability_registry,
)
from src.harness.models import (
    ActivityEventCreate,
    ActivityEventStatus,
    CapabilityDefinition,
    CapabilityHealth,
    HarnessProviderType,
    HarnessRiskTier,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)

_T0 = datetime.datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
_CAPABILITY_ID = "mcp.docs.generate_project_documentation"


async def _seed_conversation(db_session):
    user = User(username="harness-user", email="harness@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Harness", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


async def _create_run(repo, db_session, *, project_id="project-a"):
    user, conversation = await _seed_conversation(db_session)
    return await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id=project_id,
            intent_mode="generate_docs",
            capability_id=_CAPABILITY_ID,
            provider_id="mcp.docs",
            status=WorkflowRunStatus.RUNNING,
            input_summary="Generate docs for project-a",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id="thread-1",
            runtime_run_id="run-1",
            current_node="execute_capability",
            budget={"max_steps": 4},
            evidence_refs=[{"id": "evidence:1"}],
            started_at=_T0,
        )
    )


class TestHarnessWorkflowRepository:
    async def test_create_get_and_list_run_round_trip(self, db_session):
        repo = HarnessWorkflowRepository(db_session)
        run = await _create_run(repo, db_session)

        loaded = await repo.get_run(["project-a"], run.id)
        assert loaded.id == run.id
        assert loaded.project_id == "project-a"
        assert loaded.status == WorkflowRunStatus.RUNNING
        assert loaded.artifact_links == []
        assert loaded.runtime_provider_id == "langgraph.nexus"
        assert loaded.runtime_thread_id == "thread-1"
        assert loaded.current_node == "execute_capability"
        assert loaded.budget == {"max_steps": 4}
        assert loaded.evidence_refs == [{"id": "evidence:1"}]
        assert loaded.started_at == _T0

        assert await repo.list_runs([], limit=10) == []
        scoped = await repo.list_runs(["project-a"], capability_id=_CAPABILITY_ID, limit=10)
        assert [item.id for item in scoped] == [run.id]

    async def test_project_scope_blocks_cross_project_point_reads(self, db_session):
        repo = HarnessWorkflowRepository(db_session)
        run = await _create_run(repo, db_session, project_id="project-a")

        with pytest.raises(HarnessRunNotFoundError):
            await repo.get_run(["project-b"], run.id)

        with pytest.raises(HarnessRunNotFoundError):
            await repo.get_run([], run.id)

    async def test_update_run_persists_status_summary_artifacts_and_finish_time(self, db_session):
        repo = HarnessWorkflowRepository(db_session)
        run = await _create_run(repo, db_session)
        finished_at = _T0 + datetime.timedelta(seconds=5)

        updated = await repo.update_run(
            ["project-a"],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.TRIGGERED,
                output_summary="Documentation generation started",
                artifact_links=[{"label": "Dashboard", "url": "/projects/project-a"}],
                current_node="finished",
                artifact_receipts=[{"kind": "docs", "uri": "/projects/project-a/docs"}],
                runtime_metadata={"thread_status": "idle"},
                finished_at=finished_at,
            ),
        )

        assert updated.status == WorkflowRunStatus.TRIGGERED
        assert updated.output_summary == "Documentation generation started"
        assert updated.artifact_links == [{"label": "Dashboard", "url": "/projects/project-a"}]
        assert updated.current_node == "finished"
        assert updated.artifact_receipts == [{"kind": "docs", "uri": "/projects/project-a/docs"}]
        assert updated.runtime_metadata == {"thread_status": "idle"}
        assert updated.finished_at == finished_at

    async def test_activity_events_are_ordered_and_scoped_through_run(self, db_session):
        repo = HarnessWorkflowRepository(db_session)
        run = await _create_run(repo, db_session)

        await repo.add_activity_event(
            ActivityEventCreate(
                workflow_run_id=run.id,
                event_type="scope",
                label="Resolving project scope",
                status=ActivityEventStatus.SUCCEEDED,
                capability_id=run.capability_id,
                provider_id=run.provider_id,
                safe_details={"project_id": "project-a"},
                runtime_node="resolve_scope",
                tool_call_id="tool-1",
                evidence_refs=[{"id": "scope:project-a"}],
                occurred_at=_T0,
            )
        )
        await repo.add_activity_event(
            ActivityEventCreate(
                workflow_run_id=run.id,
                event_type="provider_trigger",
                label="Starting documentation generation",
                status=ActivityEventStatus.RUNNING,
                summary="MCP accepted the generation request",
                occurred_at=_T0 + datetime.timedelta(seconds=1),
            )
        )

        events = await repo.list_activity_events(["project-a"], run.id)
        assert [event.sequence for event in events] == [1, 2]
        assert events[0].safe_details == {"project_id": "project-a"}
        assert events[0].runtime_node == "resolve_scope"
        assert events[0].tool_call_id == "tool-1"
        assert events[0].evidence_refs == [{"id": "scope:project-a"}]
        assert events[1].summary == "MCP accepted the generation request"

        with pytest.raises(HarnessRunNotFoundError):
            await repo.list_activity_events(["project-b"], run.id)


class TestCapabilityRegistry:
    def test_default_registry_contains_documentation_generation_capability(self):
        registry = default_capability_registry()
        capability = registry.require_available(_CAPABILITY_ID)

        assert capability.provider_id == "mcp.docs"
        assert capability.provider_type == HarnessProviderType.DOCUMENTATION
        assert capability.risk_tier == HarnessRiskTier.TRIGGERS_WORKFLOW
        assert capability.input_schema["required"] == ["project_id"]
        assert capability.required_permissions == ["manage_projects"]

    def test_registry_rejects_unknown_and_disabled_capabilities(self):
        registry = CapabilityRegistry()

        with pytest.raises(UnknownCapabilityError):
            registry.get("missing")

        registry.register(
            CapabilityDefinition(
                id="disabled",
                label="Disabled",
                provider_type=HarnessProviderType.DOCUMENTATION,
                provider_id="docs",
                action="noop",
                output_type="none",
                risk_tier=HarnessRiskTier.READ_ONLY,
                health=CapabilityHealth.UNAVAILABLE,
            )
        )

        with pytest.raises(CapabilityDisabledError):
            registry.require_available("disabled")


class TestHarnessWorkflowService:
    async def test_service_starts_run_with_registered_provider_and_records_activity(self, db_session):
        user, conversation = await _seed_conversation(db_session)
        repo = HarnessWorkflowRepository(db_session)
        service = HarnessWorkflowService(repo, default_capability_registry())

        run = await service.start_run(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id=_CAPABILITY_ID,
            input_summary="Generate docs",
        )
        event = await service.record_activity(
            run,
            event_type="provider_trigger",
            label="Starting documentation generation",
            status=ActivityEventStatus.RUNNING,
        )
        updated = await service.mark_triggered(
            run,
            output_summary="Documentation generation started",
            artifact_links=[{"label": "Project Documentation", "url": "/docs/project-a"}],
        )

        assert run.provider_id == "mcp.docs"
        assert run.status == WorkflowRunStatus.RUNNING
        assert event.capability_id == _CAPABILITY_ID
        assert updated.status == WorkflowRunStatus.TRIGGERED
        assert updated.artifact_links == [
            {"label": "Project Documentation", "url": "/docs/project-a"}
        ]
