from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Conversation, ConversationMessage, User
from src.db.models.harness import (
    HarnessConversationStateRow,
    HarnessConversationStateTransitionRow,
    HarnessConversationTurnClaimRow,
    HarnessWorkflowRunRow,
)
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
)
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentLoopLimits,
    AgentModelError,
    AgentModelResult,
    AgentStopReason,
    non_stream_transport,
    stream_transport,
)
from src.harness.agent_loop_context import run_authoritative_agent_turn
from src.harness.conversation_state import (
    ConversationStateConflict,
    ConversationStateRepository,
    ConversationTaskEffect,
    ConversationTaskKind,
    ConversationTaskReference,
)
from src.harness.conversation_tasks import ConversationTaskService
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    DOCUMENT_UPDATE_PROPOSAL_CAPABILITY,
    PACK_DRAFT_CREATE_CAPABILITY,
    PACK_DRAFT_ABANDON_CAPABILITY,
    PACK_DRAFT_MUTATE_CAPABILITY,
    PACK_DRAFT_READ_CAPABILITY,
    PACK_DRAFT_SAVE_CAPABILITY,
    PACK_DRAFT_VALIDATE_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    ONBOARDING_RESUME_CAPABILITY,
    WorkflowRunCreate,
)
from src.harness.packs.draft_service import PackDraftService
from src.harness.providers.mcp_docs import (
    McpDocumentVersion,
    McpDocumentationGenerationResult,
    McpDocumentationGenerationStatus,
)
from src.harness.providers.mcp_projects import (
    McpProjectAttachmentResult,
    McpProjectOperationResult,
    McpProjectStatus,
    McpUploadedZipAttachmentResult,
)
from src.harness.registry import base_capability_registry
from src.harness.agent_loop import registered_capability_manifest
from src.harness.repository import HarnessWorkflowRepository
from src.harness.service import HarnessWorkflowService
from src.harness.workflow_ledger import WorkflowObservationSource
from src.harness.workflows.onboarding import OnboardingResult, ProjectOnboardingWorkflow


async def _turn(db: AsyncSession, suffix: str):
    user = User(username=f"task-{suffix}", email=f"task-{suffix}@example.com")
    db.add(user)
    await db.flush()
    conversation = Conversation(title="Tasks", user_id=user.id)
    db.add(conversation)
    await db.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="do the task",
    )
    db.add(message)
    await db.flush()
    state_repo = ConversationStateRepository(db)
    state = await state_repo.get_or_create(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    claim = await state_repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key=f"turn:{suffix}",
    )
    assert claim.lease_token is not None
    context = CapabilityExecutionContext(
        user_id=user.id,
        conversation_id=conversation.id,
        project_scope=["project-a"],
        allow_writes_content=True,
        allow_external_side_effects=True,
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    return user, conversation, state, claim, state_repo, context


async def _unclaimed_turn(db: AsyncSession, suffix: str):
    user = User(username=f"loop-{suffix}", email=f"loop-{suffix}@example.com")
    db.add(user)
    await db.flush()
    conversation = Conversation(title="Agent loop", user_id=user.id)
    db.add(conversation)
    await db.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="do the task",
    )
    db.add(message)
    await db.flush()
    repository = ConversationStateRepository(db)
    state = await repository.get_or_create(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    return user, conversation, message, state, repository


def _valid_pack_artifact() -> dict:
    return {
        "purpose": "Assess disruption and support rapid allocation decisions.",
        "analyses": [
            {"id": "exposure", "label": "Exposure", "purpose": "Map exposure."}
        ],
        "evidence_requirements": [
            {"id": "evidence", "label": "Evidence", "required": True, "weight": 100}
        ],
        "output_documents": [
            {
                "id": "brief",
                "title": "Decision Brief",
                "sections": [{"id": "summary", "heading": "Summary", "order": 1}],
            }
        ],
        "scoring_dimensions": [
            {"id": "impact", "label": "Impact", "weight": 100}
        ],
        "capability_requirements": [
            {
                "capability_id": "rag.docs.search_evidence",
                "required": True,
                "purpose": "Ground the brief",
            }
        ],
        "audience": {
            "label": "Directors",
            "roles": ["director"],
            "decision_needs": ["rapid allocation"],
            "domain_familiarity": "expert",
            "desired_outcome": "protect supply",
            "render": {
                "seniority": "director",
                "domain_familiarity": "expert",
                "decision_horizon": "immediate",
                "detail_level": "brief",
            },
        },
    }


async def test_pack_completed_call_replays_without_redispatch(
    db_session, monkeypatch
) -> None:
    user, _, _, _, state_repo, context = await _turn(db_session, "pack-replay")
    registry = base_capability_registry()
    service = PackDraftService(db_session, registry)
    original = service.create_draft
    calls = 0

    async def counted_create(**kwargs):
        nonlocal calls
        calls += 1
        return await original(**kwargs)

    monkeypatch.setattr(service, "create_draft", counted_create)
    executor = HarnessCapabilityExecutor(
        registry,
        pack_draft_service=service,
        conversation_state_repository=state_repo,
    )
    request = CapabilityExecutionRequest(
        capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
        arguments={"name": "Replay-safe Pack"},
        idempotency_key="call:create-pack",
    )

    first = await executor.execute(request, context)
    second = await executor.execute(request, context)

    assert first.status == second.status == "succeeded"
    assert first.data["draft_id"] == second.data["draft_id"]
    assert calls == 1
    assert first.task_ref == second.task_ref
    assert first.task_ref is not None and first.task_ref.draft_id is not None
    assert first.task_ref.draft_id == first.data["draft_id"]
    assert user.id == context.user_id


async def test_agent_document_update_creates_approval_without_writing(db_session) -> None:
    _, conversation, _, claim, state_repo, context = await _turn(
        db_session, "doc-update-approval"
    )
    writes = 0

    class DocsProvider:
        async def get_document_version(self, **kwargs):
            return McpDocumentVersion(
                project_id=kwargs["project_id"],
                filename=kwargs["filename"],
                sha256="sha256:authoritative-base",
            )

        async def update_doc_from_evidence(self, **kwargs):
            nonlocal writes
            writes += 1
            raise AssertionError("write ran before server approval")

    registry = base_capability_registry()
    manifest_ids = {
        item.id
        for item in registered_capability_manifest(
            registry,
            permissions=None,
            project_ids=["project-a"],
        ).tools
    }
    assert DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.id in manifest_ids
    assert DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id not in manifest_ids
    docs = DocsProvider()
    task_service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
        docs_provider=docs,  # type: ignore[arg-type]
    )
    observation = await HarnessCapabilityExecutor(
        registry,
        docs_provider=docs,  # type: ignore[arg-type]
        conversation_state_repository=state_repo,
        conversation_task_service=task_service,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_UPDATE_PROPOSAL_CAPABILITY.id,
            arguments={
                "project_id": "project-a",
                "target_doc": "security.md",
                "evidence_refs": ["evidence:scan:17"],
                "evidence_text": "Scanner evidence shows the dependency is vulnerable.",
                "section": "Dependency risks",
            },
            idempotency_key="call:doc-update-proposal",
        ),
        context.model_copy(
            update={"allow_writes_content": False, "allow_external_side_effects": False}
        ),
    )

    assert observation.status == "triggered"
    assert writes == 0
    assert observation.task_ref is not None
    assert observation.task_ref.kind is ConversationTaskKind.DOCUMENTATION_WORKFLOW
    run_id = observation.workflow_ref["workflow_run_id"]
    repo = HarnessWorkflowRepository(db_session)
    run = await repo.get_run(["project-a"], run_id, owner_user_id=context.user_id)
    interrupts = await repo.list_pending_interrupts_for_owner(run.id, context.user_id)
    assert run.intent_mode == "update_docs_from_evidence"
    assert run.status.value == "interrupted"
    assert len(interrupts) == 1
    arguments = interrupts[0].scope_snapshot["execution_arguments"]
    assert arguments["expected_base_sha"] == "sha256:authoritative-base"
    assert arguments["target_doc"] == "security.md"
    assert interrupts[0].argument_hash
    state = await state_repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="documentation_update_proposed",
        final_result={"stop_reason": "answered"},
    )
    assert state.requested_inputs[0].field == "approval"


async def test_stale_pack_call_is_typed_and_aborts_without_task_effect(db_session) -> None:
    user, conversation, state, claim, state_repo, context = await _turn(
        db_session, "pack-stale"
    )
    registry = base_capability_registry()
    service = PackDraftService(db_session, registry)
    draft = await service.create_draft(
        owner_user_id=user.id,
        conversation_id=conversation.id,
        name="Existing",
    )
    executor = HarnessCapabilityExecutor(
        registry,
        pack_draft_service=service,
        conversation_state_repository=state_repo,
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_DRAFT_MUTATE_CAPABILITY.id,
            arguments={
                "draft_id": draft.id,
                "expected_revision": draft.revision + 10,
                "artifact_patch": {"purpose": "never applied"},
            },
            idempotency_key="call:stale-mutate",
        ),
        context,
    )

    assert observation.status == "failed"
    assert observation.error_code == "pack_draft_conflict"
    assert observation.retryable is True
    claim_row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert claim_row is not None
    assert claim_row.effect_receipts[0]["phase"] == "aborted"
    assert not any("effect" in item for item in claim_row.effect_receipts)
    await state_repo.fail_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        final_result={"stop_reason": "tool_failed"},
    )
    reloaded = await state_repo.load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    assert reloaded.version == state.version
    assert reloaded.active_tasks == []


async def test_pack_mutation_denies_same_owner_cross_conversation_before_effect(
    db_session, monkeypatch
) -> None:
    user, _, _, claim, state_repo, context = await _turn(db_session, "cross-convo")
    other = Conversation(title="Other", user_id=user.id)
    db_session.add(other)
    await db_session.flush()
    registry = base_capability_registry()
    service = PackDraftService(db_session, registry)
    draft = await service.create_draft(
        conversation_id=other.id,
        owner_user_id=user.id,
        name="Other conversation",
    )
    mutated = False
    original = service.mutate

    async def counted_mutate(**kwargs):
        nonlocal mutated
        mutated = True
        return await original(**kwargs)

    monkeypatch.setattr(service, "mutate", counted_mutate)
    observation = await HarnessCapabilityExecutor(
        registry,
        pack_draft_service=service,
        conversation_state_repository=state_repo,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=PACK_DRAFT_MUTATE_CAPABILITY.id,
            arguments={
                "draft_id": draft.id,
                "expected_revision": draft.revision,
                "artifact_patch": {"purpose": "must not apply"},
            },
            idempotency_key="call:cross-convo",
        ),
        context,
    )

    assert observation.status == "denied"
    assert observation.error_code == "pack_draft_invalid"
    assert mutated is False
    claim_row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert claim_row is not None and claim_row.effect_receipts == []


async def test_pack_call_key_cannot_be_rebound_to_another_capability(db_session) -> None:
    _, _, _, claim, state_repo, _ = await _turn(db_session, "call-key")
    await state_repo.start_effect(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        call_key="same-key",
        capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
    )
    with pytest.raises(Exception, match="another capability"):
        await state_repo.start_effect(
            claim_id=claim.id,
            lease_token=claim.lease_token,
            call_key="same-key",
            capability_id=PACK_DRAFT_MUTATE_CAPABILITY.id,
        )


async def test_documentation_intent_is_committed_before_provider_call(
    db_session, engine
) -> None:
    user, _, _, claim, state_repo, context = await _turn(db_session, "docs-barrier")
    await db_session.commit()
    registry = base_capability_registry()
    service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    seen: dict[str, int | str] = {}

    async def provider_trigger() -> CapabilityObservation:
        async with session_factory() as observer:
            receipt = await observer.get(HarnessConversationTurnClaimRow, claim.id)
            assert receipt is not None
            started = receipt.effect_receipts[0]
            run_id = started["authority_hint"]["workflow_run_id"]
            run = await observer.get(HarnessWorkflowRunRow, run_id)
            assert run is not None
            seen.update(run_id=run.id, phase=started["phase"])
        return CapabilityObservation(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            status="triggered",
            output_type=DOCUMENTATION_GENERATION_CAPABILITY.output_type,
            summary="started",
            data={
                "project_id": "project-a",
                "status": "running",
                "external_run_id": "external-1",
            },
        )

    observation = await service.start_documentation(
        capability=DOCUMENTATION_GENERATION_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            idempotency_key="call:docs",
        ),
        context=context,
        provider_trigger=provider_trigger,
    )

    assert seen == {"run_id": observation.workflow_ref["workflow_run_id"], "phase": "started"}
    assert observation.task_ref is not None
    assert observation.task_ref.workflow_run_id == seen["run_id"]
    assert user.id == context.user_id


async def test_onboarding_intent_is_committed_before_graph_effects(
    db_session, engine
) -> None:
    _, _, _, claim, state_repo, context = await _turn(db_session, "onboard-barrier")
    await db_session.commit()
    registry = base_capability_registry()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    seen: dict[str, int | str] = {}

    class BarrierWorkflow:
        async def run(self, request, *, executor, context):  # noqa: ANN001
            del executor, context
            async with session_factory() as observer:
                receipt = await observer.get(HarnessConversationTurnClaimRow, claim.id)
                assert receipt is not None
                started = receipt.effect_receipts[0]
                run = await observer.get(HarnessWorkflowRunRow, request.workflow_run_id)
                assert run is not None
                assert started["authority_hint"]["workflow_run_id"] == run.id
                seen.update(run_id=run.id, phase=started["phase"])
            return OnboardingResult(
                status="interrupted",
                thread_id=f"thread-{request.workflow_run_id}",
                current_node="collect_source",
                interrupt_prompt="Choose a source.",
                interrupt_fields=["source_type"],
            )

    service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
        onboarding_workflow=BarrierWorkflow(),
    )
    observation = await service.start_onboarding(
        capability=PROJECT_ONBOARDING_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=PROJECT_ONBOARDING_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            idempotency_key="call:onboard",
        ),
        context=context,
    )

    assert seen == {"run_id": observation.workflow_ref["workflow_run_id"], "phase": "started"}
    assert observation.task_ref is not None
    assert observation.data["requested_inputs"][0]["field"] == "source_type"


def test_all_onboarding_graph_capabilities_are_registered_handlers() -> None:
    registry = base_capability_registry()
    reachable = {
        "mcp.projects.attach_git_source",
        "mcp.projects.attach_uploaded_zip_source",
        "mcp.projects.trigger_scan",
        "mcp.projects.read_status",
        "mcp.docs.generate_project_documentation",
        "mcp.docs.get_generation_status",
        "mcp.projects.publish_index",
    }
    assert all(
        registry.handler_for(capability_id).capability_id == capability_id
        for capability_id in reachable
    )


async def test_documentation_handled_failure_records_terminal_receipt_not_recovery_hold(
    db_session,
) -> None:
    user, conversation, state, claim, state_repo, context = await _turn(
        db_session, "docs-failed"
    )
    await db_session.commit()
    registry = base_capability_registry()
    service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
    )

    async def failed_provider() -> CapabilityObservation:
        return CapabilityObservation(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            status="failed",
            output_type=DOCUMENTATION_GENERATION_CAPABILITY.output_type,
            summary="Provider rejected the request.",
            error_message="Provider rejected the request.",
        )

    observation = await service.start_documentation(
        capability=DOCUMENTATION_GENERATION_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            idempotency_key="call:docs-failed",
        ),
        context=context,
        provider_trigger=failed_provider,
    )
    assert observation.status == "failed"
    claim_row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert claim_row is not None
    assert claim_row.status == "effect_started"
    assert claim_row.effect_receipts[0]["phase"] == "completed"
    assert claim_row.effect_receipts[0]["effect"]["remove_task_ref_id"].startswith(
        "workflow:"
    )
    finalized = await state_repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="tool_failed",
        final_result={"stop_reason": "tool_failed"},
    )
    assert finalized.version == state.version + 1
    assert finalized.active_tasks == []
    runs = await HarnessWorkflowRepository(db_session).list_runs(
        ["project-a"],
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    assert runs[0].status.value == "failed"


async def test_status_observation_cannot_reconcile_another_owners_external_run(
    db_session,
) -> None:
    _, _, _, _, state_repo, context = await _turn(db_session, "status-scope")
    other = User(username="other-status", email="other-status@example.com")
    db_session.add(other)
    await db_session.flush()
    other_conversation = Conversation(title="Other", user_id=other.id)
    db_session.add(other_conversation)
    await db_session.flush()
    registry = base_capability_registry()
    workflow_repo = HarnessWorkflowRepository(db_session)
    workflow_service = HarnessWorkflowService(workflow_repo, registry)
    other_run = await workflow_service.start_run(
        conversation_id=other_conversation.id,
        user_id=other.id,
        project_id="project-a",
        intent_mode="generate_docs",
        capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
        input_summary="Other owner's docs",
    )
    other_run = await workflow_service.mark_triggered(
        other_run,
        output_summary="running",
    )
    await workflow_repo.bind_child_receipt(
        ["project-a"],
        workflow_run_id=other_run.id,
        provider_id="mcp.docs",
        child_kind="documentation_generation",
        external_run_id=999,
        project_id="project-a",
        provider_status="running",
        source=WorkflowObservationSource.TRIGGER,
        owner_user_id=other.id,
    )
    task_service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
    )
    matched = await task_service.reconcile_documentation_status(
        context=context,
        requested_project_id="project-a",
        provider_project_id="project-a",
        external_run_id=999,
        provider_status="completed",
        started_at=None,
        completed_at="2026-07-19T12:00:00Z",
        error_message=None,
    )
    assert matched is None
    unchanged = await workflow_repo.get_run(
        ["project-a"], other_run.id, owner_user_id=other.id
    )
    assert unchanged.status.value == "triggered"
    assert unchanged.children[0].provider_status == "running"


@pytest.mark.parametrize(
    ("source_arguments", "attach_call"),
    [
        (
            {
                "repo_url": "https://github.com/acme/project-a.git",
                "source_type": "git",
                "platform": "generic",
            },
            "attach_git",
        ),
        (
            {
                "upload_receipt": "x" * 32,
                "source_type": "zip",
                "platform": "generic",
            },
            "attach_zip",
        ),
    ],
)
async def test_onboarding_e2e_uses_only_handlers_and_reconciles_docs_terminal(
    db_session, source_arguments, attach_call
) -> None:
    user, conversation, state, claim, state_repo, context = await _turn(
        db_session, "onboard-e2e"
    )
    await db_session.commit()
    calls: list[str] = []

    class ProjectsProvider:
        async def attach_git_repository(self, **kwargs):
            calls.append("attach_git")
            return McpProjectAttachmentResult(
                project_id=kwargs["project_id"],
                status="attached",
                platform=kwargs["platform"],
                source_type="git",
                project_path=f"/projects/{kwargs['project_id']}",
            )

        async def verify_uploaded_zip_source(self, **kwargs):
            calls.append("attach_zip")
            return McpUploadedZipAttachmentResult(
                project_id="project-a",
                platform="generic",
                source_type="zip",
                filename="project-a.zip",
                size_bytes=42,
                sha256="a" * 64,
                receipt=kwargs["upload_receipt"],
                project_path="/projects/project-a",
            )

        async def trigger_scan(self, **kwargs):
            calls.append("scan")
            return McpProjectOperationResult(
                project_id=kwargs["project_id"],
                status="accepted",
                message="scan accepted",
                status_path=f"/projects/{kwargs['project_id']}/status",
            )

        async def get_status(self, **kwargs):
            calls.append("project_status")
            return McpProjectStatus(
                project_id=kwargs["project_id"],
                status="ready",
                last_scan_at=None,
                job_count=1,
                doc_count=0,
                error_message=None,
                status_path=f"/projects/{kwargs['project_id']}/status",
                docs_path=f"/projects/{kwargs['project_id']}/docs",
            )

        async def push_to_rag(self, **kwargs):
            calls.append("publish")
            return McpProjectOperationResult(
                project_id=kwargs["project_id"],
                status="accepted",
                message="published",
                status_path=f"/projects/{kwargs['project_id']}/status",
            )

    class DocsProvider:
        async def trigger_generation(self, **kwargs):
            calls.append("docs_generate")
            return McpDocumentationGenerationResult(
                project_id=kwargs["project_id"],
                status="running",
                message="generation started",
                status_path=f"/projects/{kwargs['project_id']}/generation-status",
                docs_path=f"/projects/{kwargs['project_id']}/docs",
                run_id=77,
            )

        async def get_generation_status(self, **kwargs):
            calls.append("docs_status")
            return McpDocumentationGenerationStatus(
                project_id=kwargs["project_id"],
                found=True,
                run_id=77,
                status="completed",
                started_at="2026-07-19T10:00:00Z",
                completed_at="2026-07-19T10:01:00Z",
                doc_count=3,
                quality_score=1.0,
                run_verdict="pass",
                client_ready=True,
                failed_gates=[],
                error_message=None,
                status_path=f"/projects/{kwargs['project_id']}/generation-status",
                docs_path=f"/projects/{kwargs['project_id']}/docs",
            )

    registry = base_capability_registry()
    docs = DocsProvider()
    service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
        docs_provider=docs,
        projects_provider=ProjectsProvider(),
        onboarding_workflow=ProjectOnboardingWorkflow(
            scan_wait_timeout=0.2,
            scan_poll_interval=0,
        ),
    )
    observation = await service.start_onboarding(
        capability=PROJECT_ONBOARDING_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=PROJECT_ONBOARDING_CAPABILITY.id,
                arguments={
                    "project_id": "project-a",
                    **source_arguments,
            },
            idempotency_key="call:onboard-e2e",
        ),
        context=context,
    )
    assert observation.status == "succeeded"
    assert calls[:6] == [
        attach_call,
        "scan",
        "project_status",
        "docs_generate",
        "docs_status",
        "publish",
    ]
    active = await state_repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="onboarding_complete",
        final_result={"stop_reason": "answered"},
    )
    assert active.active_tasks == []
    assert active.objective is None
    workflow_repo = HarnessWorkflowRepository(db_session)
    runs = await workflow_repo.list_runs(
        ["project-a"],
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    onboarding = next(item for item in runs if item.capability_id == PROJECT_ONBOARDING_CAPABILITY.id)
    docs_run = next(
        item
        for item in runs
        if item.capability_id == DOCUMENTATION_GENERATION_CAPABILITY.id
    )
    assert onboarding.status.value == "succeeded"
    assert docs_run.status.value == "succeeded"
    assert docs_run.children[0].external_run_id == "77"
    terminal = await state_repo.reconcile_authorities(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    assert terminal.active_tasks == []
    assert terminal.version == state.version + 1


class _ScriptedModel:
    def __init__(self, *items):
        self.items = list(items)
        self.calls = 0
        self.requests = []

    async def decide(self, request):  # noqa: ANN001
        self.requests.append(request)
        item = self.items[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return AgentModelResult(decision=item)


async def test_authoritative_loop_keeps_pack_active_while_answering_unrelated_question(
    db_session,
) -> None:
    user, conversation, first_message, _, state_repo = await _unclaimed_turn(
        db_session, "pack"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    registry = base_capability_registry()
    draft_service = PackDraftService(db_session, registry)
    executor = HarnessCapabilityExecutor(
        registry,
        pack_draft_service=draft_service,
        conversation_state_repository=state_repo,
    )
    first_model = _ScriptedModel(
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
            arguments={"name": "Agentic Pack"},
        ),
        AgentDecision(
            action=AgentBoundaryAction.ANSWER,
            content="I created the Pack draft.",
        ),
    )
    first = await run_authoritative_agent_turn(
        conversation=conversation,
        query=first_message.content,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=executor,
        model=first_model,
        message_id=first_message.id,
        state_repository=state_repo,
        allow_writes_content=True,
    )
    assert first.stop_reason is AgentStopReason.ANSWERED
    assert all(
        item.get("content") != first_message.content
        for item in first_model.requests[0].conversation_history
    )
    state = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert len(state.active_tasks) == 1
    assert state.active_tasks[0].kind.value == "pack_draft"

    unrelated = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="What is the deployment status?",
    )
    db_session.add(unrelated)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    second_model = _ScriptedModel(
        AgentDecision(
            action=AgentBoundaryAction.ANSWER,
            content="No deployment evidence was requested from a tool.",
            complete_objective=True,
        )
    )
    second = await run_authoritative_agent_turn(
        conversation=conversation,
        query=unrelated.content,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=executor,
        model=second_model,
        message_id=unrelated.id,
        state_repository=state_repo,
        allow_writes_content=True,
    )
    assert second.stop_reason is AgentStopReason.ANSWERED
    assert second.complete_objective is False
    assert second_model.calls == 1
    after = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert [item.task_ref_id for item in after.active_tasks] == [
        state.active_tasks[0].task_ref_id
    ]
    assert after.objective == state.objective
    replay = await run_authoritative_agent_turn(
        conversation=conversation,
        query=unrelated.content,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=executor,
        model=_ScriptedModel(AssertionError("applied turn must replay")),
        message_id=unrelated.id,
        state_repository=state_repo,
        allow_writes_content=True,
    )
    assert replay == second


async def test_authoritative_loop_replays_applied_message_without_model_call(
    db_session,
) -> None:
    user, conversation, message, _, state_repo = await _unclaimed_turn(
        db_session, "replay"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    replay_message_id = message.id
    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(registry)
    model = _ScriptedModel(
        AgentDecision(action=AgentBoundaryAction.ANSWER, content="Exact answer.")
    )
    first = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=executor,
        model=model,
        message_id=replay_message_id,
        state_repository=state_repo,
    )
    transitions_before = list(
        (
            await db_session.execute(
                select(HarnessConversationStateTransitionRow).where(
                    HarnessConversationStateTransitionRow.message_id == message.id
                )
            )
        ).scalars()
    )
    assert len(transitions_before) == 1
    assert non_stream_transport(first)["output"] == "Exact answer."
    assert [event async for event in stream_transport(first)][-1][0] == "agent_final"
    transitions_after = list(
        (
            await db_session.execute(
                select(HarnessConversationStateTransitionRow).where(
                    HarnessConversationStateTransitionRow.message_id == message.id
                )
            )
        ).scalars()
    )
    assert [item.id for item in transitions_after] == [
        item.id for item in transitions_before
    ]
    replay_model = _ScriptedModel(
        AssertionError("model must not run for an applied message")
    )
    replay = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=executor,
        model=replay_model,
        message_id=replay_message_id,
        state_repository=state_repo,
    )
    assert replay == first
    assert replay_model.calls == 0


async def test_agent_explicitly_completes_taskless_objective_but_unrelated_answer_does_not(
    db_session,
) -> None:
    user, conversation, unrelated, state, state_repo = await _unclaimed_turn(
        db_session, "standalone-resolution"
    )
    row = await db_session.get(HarnessConversationStateRow, state.id)
    assert row is not None
    row.objective = "Choose the reporting period"
    row.requested_inputs = [
        {
            "task_ref_id": None,
            "field": "reporting_period",
            "prompt": "Which reporting period?",
            "options": ["January", "February"],
        }
    ]
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    registry = base_capability_registry()
    unrelated_result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=unrelated.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=_ScriptedModel(
            AgentDecision(
                action=AgentBoundaryAction.ANSWER,
                content="That unrelated question is answered.",
            )
        ),
        message_id=unrelated.id,
        state_repository=state_repo,
    )
    assert unrelated_result.complete_objective is False
    pending = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert pending.objective == "Choose the reporting period"
    assert pending.requested_inputs[0].field == "reporting_period"

    resolution = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Use January.",
    )
    db_session.add(resolution)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    resolved_result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=resolution.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=_ScriptedModel(
            AgentDecision(
                action=AgentBoundaryAction.ANSWER,
                content="I’ll use January.",
                complete_objective=True,
            )
        ),
        message_id=resolution.id,
        state_repository=state_repo,
    )
    assert resolved_result.complete_objective is True
    resolved = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert resolved.objective is None
    assert resolved.requested_inputs == []


async def test_no_effect_loop_failure_releases_claim_without_state_version_change(
    db_session,
) -> None:
    user, conversation, _, initial, state_repo = await _unclaimed_turn(
        db_session, "failure"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="fail safely",
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    registry = base_capability_registry()
    result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=_ScriptedModel(AgentModelError(AgentStopReason.PLANNER_UNAVAILABLE)),
        message_id=message.id,
        state_repository=state_repo,
    )
    assert result.stop_reason is AgentStopReason.PLANNER_UNAVAILABLE
    state = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert state.version == initial.version
    claim_row = (
        await db_session.execute(
            select(HarnessConversationTurnClaimRow).where(
                HarnessConversationTurnClaimRow.message_id == message.id
            )
        )
    ).scalar_one()
    assert claim_row.status == "failed"


async def test_raised_executor_exception_rolls_back_and_closes_durable_claim(
    db_session,
) -> None:
    user, conversation, message, state, state_repo = await _unclaimed_turn(
        db_session, "raised-executor"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    raised_message_id = message.id
    registry = base_capability_registry()

    class RaisingExecutor:
        async def execute(self, request, context):  # noqa: ANN001
            del request, context
            raise RuntimeError("unexpected executor failure")

    result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=RaisingExecutor(),  # type: ignore[arg-type]
        model=_ScriptedModel(
            AgentDecision(
                action=AgentBoundaryAction.CALL_CAPABILITY,
                capability_id=PACK_DRAFT_READ_CAPABILITY.id,
                arguments={"draft_id": 1},
            )
        ),
        message_id=raised_message_id,
        state_repository=state_repo,
    )
    assert result.stop_reason is AgentStopReason.PLANNER_UNAVAILABLE
    claim = (
        await db_session.execute(
            select(HarnessConversationTurnClaimRow).where(
                HarnessConversationTurnClaimRow.message_id == raised_message_id
            )
        )
    ).scalar_one()
    assert claim.status == "failed"
    assert (
        await state_repo.load(conversation_id=conversation.id, owner_user_id=user.id)
    ).version == state.version


async def test_finalizer_state_conflict_closes_no_effect_claim(
    db_session, monkeypatch
) -> None:
    user, conversation, message, _, state_repo = await _unclaimed_turn(
        db_session, "finalize-conflict"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    finalizer_message_id = message.id

    async def lose_cas(**kwargs):
        del kwargs
        raise ConversationStateConflict("forced CAS loss")

    monkeypatch.setattr(state_repo, "finalize_claim", lose_cas)
    registry = base_capability_registry()
    result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=_ScriptedModel(
            AgentDecision(action=AgentBoundaryAction.ANSWER, content="Answer.")
        ),
        message_id=finalizer_message_id,
        state_repository=state_repo,
    )
    assert result.stop_reason is AgentStopReason.STATE_CONFLICT
    claim = (
        await db_session.execute(
            select(HarnessConversationTurnClaimRow).where(
                HarnessConversationTurnClaimRow.message_id == finalizer_message_id
            )
        )
    ).scalar_one()
    assert claim.status == "failed"


@pytest.mark.parametrize("operation", ["read", "mutate"])
async def test_pack_read_or_mutate_observation_refocuses_pack_through_loop(
    db_session, operation
) -> None:
    user, conversation, setup_message, state, state_repo = await _unclaimed_turn(
        db_session, f"focus-{operation}"
    )
    registry = base_capability_registry()
    draft_service = PackDraftService(db_session, registry)
    draft = await draft_service.create_draft(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        name="Focus Pack",
    )
    workflow = await HarnessWorkflowRepository(db_session).create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="generate_docs",
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            provider_id="mcp.docs",
            input_summary="Generate docs",
        )
    )
    setup_claim = await state_repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=setup_message.id,
        expected_version=state.version,
        idempotency_key=f"setup:{operation}",
    )
    assert setup_claim.lease_token
    for call_key, task in (
        (
            "setup-pack",
            ConversationTaskReference(
                task_ref_id=f"pack:{draft.id}",
                kind=ConversationTaskKind.PACK_DRAFT,
                capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
                draft_id=draft.id,
                focused=True,
            ),
        ),
        (
            "setup-workflow",
            ConversationTaskReference(
                task_ref_id=f"workflow:{workflow.id}",
                kind=ConversationTaskKind.DOCUMENTATION_WORKFLOW,
                capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
                workflow_run_id=workflow.id,
                project_id="project-a",
                focused=True,
            ),
        ),
    ):
        await state_repo.start_effect(
            claim_id=setup_claim.id,
            lease_token=setup_claim.lease_token,
            call_key=call_key,
            capability_id=task.capability_id,
        )
        await state_repo.record_effect(
            claim_id=setup_claim.id,
            lease_token=setup_claim.lease_token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=task.capability_id,
                task_ref=task,
            ),
        )
    state = await state_repo.finalize_claim(
        claim_id=setup_claim.id,
        lease_token=setup_claim.lease_token,
        action="setup",
        final_result={"stop_reason": "answered"},
    )
    current = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=f"{operation} the Pack",
    )
    db_session.add(current)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    if operation == "read":
        tool = AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id="nexus.packs.drafts.read",
            arguments={"draft_id": draft.id},
        )
    else:
        tool = AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_MUTATE_CAPABILITY.id,
            arguments={
                "draft_id": draft.id,
                "expected_revision": draft.revision,
                "artifact_patch": {"purpose": "Focused mutation"},
            },
        )
    result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=current.content,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            pack_draft_service=draft_service,
            conversation_state_repository=state_repo,
        ),
        model=_ScriptedModel(
            tool,
            AgentDecision(action=AgentBoundaryAction.ANSWER, content="Done."),
        ),
        message_id=current.id,
        state_repository=state_repo,
        allow_writes_content=True,
    )
    assert result.stop_reason is AgentStopReason.ANSWERED
    focused = await state_repo.load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    # One authority-hydration transition fills Pack validation prompts, then
    # exactly one user-turn transition persists the focus/result.
    assert focused.version == state.version + 2
    assert [item.task_ref_id for item in focused.active_tasks if item.focused] == [
        f"pack:{draft.id}"
    ]


async def test_full_registered_pack_lifecycle_runs_in_one_bounded_loop(db_session) -> None:
    user, conversation, message, _, state_repo = await _unclaimed_turn(
        db_session, "full-pack"
    )
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    registry = base_capability_registry()
    draft_service = PackDraftService(db_session, registry)
    model = _ScriptedModel(
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
            arguments={"name": "Full Lifecycle", "artifact_patch": _valid_pack_artifact()},
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_READ_CAPABILITY.id,
            arguments={"draft_id": 1},
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_MUTATE_CAPABILITY.id,
            arguments={
                "draft_id": 1,
                "expected_revision": 1,
                "artifact_patch": {
                    "purpose": "Assess disruption and support 48-hour allocation decisions."
                },
            },
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_VALIDATE_CAPABILITY.id,
            arguments={"draft_id": 1, "expected_revision": 2},
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_SAVE_CAPABILITY.id,
            arguments={"draft_id": 1, "expected_revision": 2},
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_CREATE_CAPABILITY.id,
            arguments={"name": "Discard Me"},
        ),
        AgentDecision(
            action=AgentBoundaryAction.CALL_CAPABILITY,
            capability_id=PACK_DRAFT_ABANDON_CAPABILITY.id,
            arguments={"draft_id": 2, "expected_revision": 1},
        ),
        AgentDecision(
            action=AgentBoundaryAction.ANSWER,
            content="The first Pack was saved and the second draft abandoned.",
        ),
    )
    result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=message.content,
        db=db_session,
        user_id=user.id,
        project_scope=[],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            pack_draft_service=draft_service,
            conversation_state_repository=state_repo,
        ),
        model=model,
        limits=AgentLoopLimits(max_iterations=8, max_tool_calls=7),
        approvals={PACK_DRAFT_SAVE_CAPABILITY.id, PACK_DRAFT_ABANDON_CAPABILITY.id},
        allow_writes_content=True,
        message_id=message.id,
        state_repository=state_repo,
    )
    assert result.stop_reason is AgentStopReason.ANSWERED
    assert [item.capability_id for item in result.observations] == [
        PACK_DRAFT_CREATE_CAPABILITY.id,
        PACK_DRAFT_READ_CAPABILITY.id,
        PACK_DRAFT_MUTATE_CAPABILITY.id,
        PACK_DRAFT_VALIDATE_CAPABILITY.id,
        PACK_DRAFT_SAVE_CAPABILITY.id,
        PACK_DRAFT_CREATE_CAPABILITY.id,
        PACK_DRAFT_ABANDON_CAPABILITY.id,
    ]
    assert all(item.succeeded for item in result.observations)
    state = await state_repo.load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
    )
    assert state.active_tasks == []
    assert state.objective is None
    assert (await draft_service.get_draft(1, user.id)).status == "saved"
    assert (await draft_service.get_draft(2, user.id)).status == "abandoned"


async def test_onboarding_interrupt_allows_unrelated_answer_then_exact_resume(
    db_session,
) -> None:
    user, conversation, start_message, state, state_repo = await _unclaimed_turn(
        db_session, "interrupt-resume"
    )
    registry = base_capability_registry()
    workflow = ProjectOnboardingWorkflow(scan_wait_timeout=0.1, scan_poll_interval=0)
    service = ConversationTaskService(
        db=db_session,
        registry=registry,
        state_repository=state_repo,
        onboarding_workflow=workflow,
    )
    start_claim = await state_repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=start_message.id,
        expected_version=state.version,
        idempotency_key="turn:onboard-interrupt",
    )
    assert start_claim.lease_token
    start_context = CapabilityExecutionContext(
        user_id=user.id,
        conversation_id=conversation.id,
        project_scope=["project-a"],
        current_message_id=start_message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=start_claim.id,
        conversation_turn_claim_token=start_claim.lease_token,
    )
    started = await service.start_onboarding(
        capability=PROJECT_ONBOARDING_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=PROJECT_ONBOARDING_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            idempotency_key="call:onboard-interrupt",
        ),
        context=start_context,
    )
    assert started.status == "triggered"
    assert started.data["requested_inputs"][0]["field"] == "source_type"
    await state_repo.finalize_claim(
        claim_id=start_claim.id,
        lease_token=start_claim.lease_token,
        action="clarify",
        final_result={"stop_reason": "clarification_required"},
    )
    workflow_run_id = started.workflow_ref["workflow_run_id"]

    unrelated = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="What does the Pack policy allow?",
    )
    db_session.add(unrelated)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    unrelated_result = await run_authoritative_agent_turn(
        conversation=conversation,
        query=unrelated.content,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        permissions=None,
        access_token=None,
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        model=_ScriptedModel(
            AgentDecision(
                action=AgentBoundaryAction.ANSWER,
                content="The workflow is still waiting for its exact source input.",
            )
        ),
        message_id=unrelated.id,
        state_repository=state_repo,
    )
    assert unrelated_result.stop_reason is AgentStopReason.ANSWERED
    after_answer = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert after_answer.active_tasks[0].workflow_run_id == workflow_run_id

    resume_message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Use Git.",
    )
    db_session.add(resume_message)
    await db_session.flush()
    run = await HarnessWorkflowRepository(db_session).get_run(
        ["project-a"], workflow_run_id, owner_user_id=user.id
    )
    resume_claim = await state_repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=resume_message.id,
        expected_version=after_answer.version,
        idempotency_key="turn:onboard-resume",
    )
    assert resume_claim.lease_token
    resume_context = start_context.model_copy(
        update={
            "current_message_id": resume_message.id,
            "conversation_state_version": after_answer.version,
            "conversation_turn_claim_id": resume_claim.id,
            "conversation_turn_claim_token": resume_claim.lease_token,
        }
    )
    resume_executor = HarnessCapabilityExecutor(
        registry,
        conversation_state_repository=state_repo,
        conversation_task_service=service,
    )
    out_of_scope = await resume_executor.execute(
        CapabilityExecutionRequest(
            capability_id=ONBOARDING_RESUME_CAPABILITY.id,
            arguments={
                "workflow_run_id": workflow_run_id,
                "expected_version": run.version,
                "inputs": {"source_type": "git"},
            },
            idempotency_key="call:onboard-out-of-scope",
        ),
        resume_context.model_copy(update={"project_scope": ["project-b"]}),
    )
    assert out_of_scope.status == "denied"
    assert out_of_scope.error_code == "conversation_task_invalid"
    assert out_of_scope.summary == "Onboarding workflow is unavailable"
    assert "workflow_run_id" not in out_of_scope.summary
    resume_claim_row = await db_session.get(
        HarnessConversationTurnClaimRow, resume_claim.id
    )
    assert resume_claim_row is not None and resume_claim_row.effect_receipts == []
    stale = await resume_executor.execute(
        CapabilityExecutionRequest(
            capability_id=ONBOARDING_RESUME_CAPABILITY.id,
            arguments={
                "workflow_run_id": workflow_run_id,
                "expected_version": run.version - 1,
                "inputs": {"source_type": "git"},
            },
            idempotency_key="call:onboard-stale",
        ),
        resume_context,
    )
    wrong_input = await resume_executor.execute(
        CapabilityExecutionRequest(
            capability_id=ONBOARDING_RESUME_CAPABILITY.id,
            arguments={
                "workflow_run_id": workflow_run_id,
                "expected_version": run.version,
                "inputs": {"platform": "generic"},
            },
            idempotency_key="call:onboard-wrong-input",
        ),
        resume_context,
    )
    assert stale.status == "failed"
    assert stale.error_code == "conversation_task_conflict"
    assert stale.retryable is True
    assert wrong_input.status == "denied"
    assert wrong_input.error_code == "conversation_task_invalid"
    resume_claim_row = await db_session.get(
        HarnessConversationTurnClaimRow, resume_claim.id
    )
    assert resume_claim_row is not None and resume_claim_row.effect_receipts == []
    resumed = await service.resume_onboarding(
        capability=ONBOARDING_RESUME_CAPABILITY,
        request=CapabilityExecutionRequest(
            capability_id=ONBOARDING_RESUME_CAPABILITY.id,
            arguments={
                "workflow_run_id": workflow_run_id,
                "expected_version": run.version,
                "inputs": {"source_type": "git"},
            },
            idempotency_key="call:onboard-resume",
        ),
        context=resume_context,
    )
    assert resumed.workflow_ref["workflow_run_id"] == workflow_run_id
    assert resumed.data["requested_inputs"][0]["field"] == "repo_url"
    await state_repo.finalize_claim(
        claim_id=resume_claim.id,
        lease_token=resume_claim.lease_token,
        action="clarify",
        final_result={"stop_reason": "clarification_required"},
    )
    final = await state_repo.load(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert final.active_tasks[0].workflow_run_id == workflow_run_id
    assert final.requested_inputs[0].field == "repo_url"
