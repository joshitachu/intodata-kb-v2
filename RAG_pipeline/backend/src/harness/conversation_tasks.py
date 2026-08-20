"""Registered Pack-independent onboarding and documentation task orchestration."""

from __future__ import annotations

import datetime
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.harness import HarnessRunNotFoundError
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    ConversationTaskConflictError,
    ConversationTaskExecutionError,
    ConversationTaskInputError,
)
from src.harness.conversation_state import (
    ConversationEffectReceipt,
    ConversationEntityReference,
    ConversationRequestedInput,
    ConversationStateRepository,
    ConversationTaskEffect,
    ConversationTaskKind,
    ConversationTaskReference,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    SUPPORTED_PROJECT_PLATFORMS,
    CapabilityDefinition,
    WorkflowInterruptType,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.registry import CapabilityRegistry
from src.harness.reconciliation import DocumentationWorkflowCallbackIngestor
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository
from src.harness.service import HarnessWorkflowService
from src.harness.workflow_ledger import WorkflowObservationSource
from src.harness.workflows.onboarding import (
    OnboardingRequest,
    OnboardingResult,
    ProjectOnboardingWorkflow,
    default_project_onboarding_workflow,
)
from src.harness.workflows.documentation_update import (
    DocumentationUpdateRequest,
    default_documentation_update_workflow,
)


ProviderTrigger = Callable[[], Awaitable[CapabilityObservation]]


class OnboardingResumeInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str | None = Field(default=None, pattern="^(git|zip)$")
    repo_url: str | None = Field(default=None, min_length=1, max_length=2000)
    upload_receipt: str | None = Field(default=None, min_length=32, max_length=4096)
    platform: str | None = None

    @model_validator(mode="after")
    def supported_and_nonempty(self) -> "OnboardingResumeInputs":
        if self.platform is not None and self.platform not in SUPPORTED_PROJECT_PLATFORMS:
            raise ValueError("Unsupported project platform")
        if not any(
            value is not None
            for value in (self.source_type, self.repo_url, self.upload_receipt, self.platform)
        ):
            raise ValueError("At least one requested onboarding input is required")
        return self


class ConversationTaskService:
    """Create/resume workflow parents and return typed task observations."""

    def __init__(
        self,
        *,
        db,
        registry: CapabilityRegistry,
        state_repository: ConversationStateRepository,
        docs_provider: McpDocumentationProvider | None = None,
        projects_provider: McpProjectsProvider | None = None,
        onboarding_workflow: ProjectOnboardingWorkflow | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._state = state_repository
        self._docs = docs_provider or McpDocumentationProvider()
        self._projects = projects_provider or McpProjectsProvider()
        self._onboarding = onboarding_workflow or default_project_onboarding_workflow()
        self._workflow_repo = HarnessWorkflowRepository(db)
        self._workflow_service = HarnessWorkflowService(self._workflow_repo, registry)

    async def start_documentation(
        self,
        *,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        provider_trigger: ProviderTrigger,
    ) -> CapabilityObservation:
        claim_id, token, call_key, conversation_id = _claim(context, request)
        project_id = str(request.arguments["project_id"])
        replay = await self._state.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={"kind": "workflow_create", "project_id": project_id},
        )
        if replay is not None:
            return await self._replay_workflow(capability, replay, context)
        run = await self._workflow_service.start_run(
            conversation_id=conversation_id,
            user_id=context.user_id,
            project_id=project_id,
            intent_mode="generate_docs",
            capability_id=capability.id,
            input_summary="Generate project documentation",
        )
        await self._state.set_effect_authority(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            authority_hint={
                "kind": "workflow_run",
                "workflow_run_id": run.id,
                "project_id": project_id,
            },
        )
        # The claim receipt and exact local workflow identity must survive a
        # process crash before the external provider sees this request.
        await self._db.commit()
        try:
            provider_observation = await provider_trigger()
        except Exception as exc:
            run = await self._workflow_service.mark_failed(
                run, error_message="Documentation generation failed to start."
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            raise exc
        if not provider_observation.succeeded:
            run = await self._workflow_service.mark_failed(
                run,
                error_message=(
                    provider_observation.error_message
                    or provider_observation.summary
                    or "Documentation generation failed to start."
                ),
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            return provider_observation
        run = await self._workflow_service.mark_triggered(
            run,
            output_summary=provider_observation.summary,
            artifact_links=provider_observation.artifact_links,
        )
        external_run_id = provider_observation.data.get("external_run_id")
        if external_run_id is not None:
            await self._workflow_repo.bind_child_receipt(
                [project_id],
                workflow_run_id=run.id,
                provider_id=capability.provider_id,
                child_kind="documentation_generation",
                external_run_id=external_run_id,
                project_id=project_id,
                provider_status=str(
                    provider_observation.data.get("status") or "generation_triggered"
                ),
                source=WorkflowObservationSource.TRIGGER,
                owner_user_id=context.user_id,
            )
            run = await self._workflow_repo.get_run(
                [project_id], run.id, owner_user_id=context.user_id
            )
        task = _workflow_task(
            run.id,
            ConversationTaskKind.DOCUMENTATION_WORKFLOW,
            capability.id,
            project_id,
        )
        await self._state.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability.id,
                task_ref=task,
                objective=f"Generate documentation for {project_id}",
                referenced_entities=[
                    ConversationEntityReference(
                        entity_type="project",
                        stable_id=project_id,
                        display_name=project_id,
                        project_id=project_id,
                    )
                ],
            ),
            domain_ref={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "status": run.status.value,
            },
        )
        await self._db.commit()
        return provider_observation.model_copy(
            update={
                "workflow_ref": {
                    "workflow_run_id": run.id,
                    "external_run_id": external_run_id,
                    "provider_id": capability.provider_id,
                    "project_id": project_id,
                    "status": run.status.value,
                },
                "task_ref": task,
            }
        )

    async def start_onboarding(
        self,
        *,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        claim_id, token, call_key, conversation_id = _claim(context, request)
        project_id = str(request.arguments["project_id"])
        replay = await self._state.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={"kind": "workflow_create", "project_id": project_id},
        )
        if replay is not None:
            return await self._replay_workflow(capability, replay, context)
        run = await self._workflow_service.start_run(
            conversation_id=conversation_id,
            user_id=context.user_id,
            project_id=project_id,
            intent_mode="project_onboarding",
            capability_id=capability.id,
            input_summary="Onboard project source",
        )
        await self._state.set_effect_authority(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            authority_hint={
                "kind": "workflow_run",
                "workflow_run_id": run.id,
                "project_id": project_id,
            },
        )
        await self._db.commit()
        onboarding_request = OnboardingRequest(
            workflow_run_id=run.id,
            project_id=project_id,
            repo_url=request.arguments.get("repo_url"),
            source_type=request.arguments.get("source_type"),
            platform=request.arguments.get("platform") or "generic",
            installation_id=request.arguments.get("installation_id"),
            upload_receipt=request.arguments.get("upload_receipt"),
            plugin_pack_id=request.arguments.get("plugin_pack_id"),
        )
        try:
            result = await self._onboarding.run(
                onboarding_request,
                executor=self._subordinate_executor(),
                context=_subordinate_context(context, project_id),
            )
        except Exception as exc:
            run = await self._workflow_service.mark_failed(
                run,
                error_message="Project onboarding failed during execution.",
                runtime_provider_id="langgraph.nexus",
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            raise ConversationTaskExecutionError(
                "Project onboarding failed during execution."
            ) from exc
        return await self._finish_onboarding(
            run=run,
            result=result,
            capability=capability,
            claim_id=claim_id,
            token=token,
            call_key=call_key,
            project_id=project_id,
            arguments=onboarding_request.model_dump(mode="json"),
        )

    async def start_documentation_update_proposal(
        self,
        *,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        """Create a durable approval interrupt without performing the write."""

        claim_id, token, call_key, conversation_id = _claim(context, request)
        project_id = str(request.arguments["project_id"])
        target_doc = str(request.arguments["target_doc"])
        replay = await self._state.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={"kind": "workflow_create", "project_id": project_id},
        )
        if replay is not None:
            return await self._replay_workflow(capability, replay, context)
        run = await self._workflow_service.start_run(
            conversation_id=conversation_id,
            user_id=context.user_id,
            project_id=project_id,
            intent_mode="update_docs_from_evidence",
            capability_id=capability.id,
            input_summary="Propose evidence-backed documentation update",
        )
        await self._state.set_effect_authority(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            authority_hint={
                "kind": "workflow_run",
                "workflow_run_id": run.id,
                "project_id": project_id,
            },
        )
        await self._db.commit()
        try:
            preview = await self._docs.preview_chat_patch(
                project_id=project_id,
                filename=target_doc,
                section=str(request.arguments.get("section") or ""),
                replacement_text=str(request.arguments["evidence_text"]),
                evidence_refs=[
                    str(item) for item in request.arguments.get("evidence_refs", [])
                ],
                assertion_kind=str(request.arguments.get("assertion_kind") or "editorial"),
                access_token=context.access_token,
            )
            arguments = {
                "project_id": project_id,
                "target_doc": target_doc,
                "evidence_refs": [
                    str(item) for item in request.arguments.get("evidence_refs", [])
                ],
                "evidence_text": str(request.arguments["evidence_text"]),
                "expected_base_sha": preview.base_document_sha256,
                "section": request.arguments.get("section"),
                "workflow_run_id": str(run.id),
                "proposal_id": preview.proposal_id,
                "patch_plan_hash": preview.patch_plan_hash,
                "base_knowledge_revision_id": preview.base_knowledge_revision_id,
                "compact_diff": preview.compact_diff,
                "changed_scope": preview.changed_scope,
                "assertion_kind": preview.assertion_kind,
                "expires_at": preview.expires_at,
            }
            result = await default_documentation_update_workflow().run(
                DocumentationUpdateRequest(
                    workflow_run_id=run.id,
                    project_id=project_id,
                    target_doc=target_doc,
                    evidence_refs=arguments["evidence_refs"],
                    evidence_text=arguments["evidence_text"],
                    expected_base_sha=arguments["expected_base_sha"],
                    section=arguments["section"],
                    proposal_id=arguments["proposal_id"],
                    patch_plan_hash=arguments["patch_plan_hash"],
                    base_knowledge_revision_id=arguments["base_knowledge_revision_id"],
                    compact_diff=arguments["compact_diff"],
                    changed_scope=arguments["changed_scope"],
                    assertion_kind=arguments["assertion_kind"],
                    expires_at=arguments["expires_at"],
                ),
                executor=self._subordinate_executor(),
                context=context.model_copy(update={"project_scope": [project_id]}),
            )
        except Exception as exc:
            run = await self._workflow_service.mark_failed(
                run,
                error_message="Documentation update proposal could not be prepared.",
                runtime_provider_id="langgraph.nexus",
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            raise ConversationTaskExecutionError(
                "Documentation update proposal could not be prepared."
            ) from exc
        if result.status != "interrupted" or result.interrupt_id is None:
            run = await self._workflow_service.mark_failed(
                run,
                error_message="Documentation update approval was not created.",
                runtime_provider_id="langgraph.nexus",
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary="Documentation update approval was not created.",
                error_message="Documentation update approval was not created.",
            )
        run = await self._workflow_service.mark_interrupted(
            run,
            output_summary="Approval is required before updating documentation.",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "documentation_update",
                "proposal": {
                    "target_doc": target_doc,
                    "section": arguments["section"],
                    "evidence_refs": arguments["evidence_refs"],
                    "expected_base_sha": arguments["expected_base_sha"],
                    "base_knowledge_revision_id": arguments["base_knowledge_revision_id"],
                    "patch_plan_hash": arguments["patch_plan_hash"],
                    "compact_diff": arguments["compact_diff"],
                    "changed_scope": arguments["changed_scope"],
                    "assertion_kind": arguments["assertion_kind"],
                    "expires_at": arguments["expires_at"],
                },
                "interrupt": {
                    "langgraph_interrupt_id": result.interrupt_id,
                    "type": "approval",
                },
            },
            interrupt_type=WorkflowInterruptType.APPROVAL,
            prompt=f"Update `{target_doc}` in `{project_id}` from the supplied evidence?",
            policy_snapshot={
                "risk": "writes_content",
                "write_capability_id": DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id,
            },
            scope_snapshot={
                "project_id": project_id,
                "execution_arguments": arguments,
            },
            normalized_arguments=arguments,
        )
        task = _workflow_task(
            run.id,
            ConversationTaskKind.DOCUMENTATION_WORKFLOW,
            capability.id,
            project_id,
        )
        requested = ConversationRequestedInput(
            task_ref_id=task.task_ref_id,
            field="approval",
            prompt=f"Approve the update to `{target_doc}`?",
            options=["approve", "decline"],
        )
        await self._state.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability.id,
                task_ref=task,
                objective=f"Update {target_doc} for {project_id}",
                referenced_entities=[
                    ConversationEntityReference(
                        entity_type="project",
                        stable_id=project_id,
                        display_name=project_id,
                        project_id=project_id,
                    ),
                    ConversationEntityReference(
                        entity_type="document",
                        stable_id=target_doc,
                        display_name=target_doc,
                        project_id=project_id,
                    ),
                ],
                requested_inputs=[requested],
            ),
            domain_ref={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "status": run.status.value,
            },
        )
        await self._db.commit()
        return CapabilityObservation(
            capability_id=capability.id,
            status="triggered",
            output_type=capability.output_type,
            summary="Approval is required before updating documentation.",
            data={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "target_doc": target_doc,
                "status": run.status.value,
                "requested_inputs": [requested.model_dump(mode="json")],
            },
            workflow_ref={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "status": run.status.value,
            },
            task_ref=task,
        )

    async def resume_onboarding(
        self,
        *,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        claim_id, token, call_key, _ = _claim(context, request)
        workflow_run_id = int(request.arguments["workflow_run_id"])
        expected_version = int(request.arguments["expected_version"])
        try:
            inputs = OnboardingResumeInputs.model_validate(request.arguments["inputs"])
        except ValueError as exc:
            raise ConversationTaskInputError("Onboarding input is invalid") from exc
        try:
            run = await self._workflow_repo.get_run(
                context.project_scope,
                workflow_run_id,
                owner_user_id=context.user_id,
            )
        except HarnessRunNotFoundError as exc:
            # Keep existence, ownership, and project-scope failures indistinguishable.
            raise ConversationTaskInputError(
                "Onboarding workflow is unavailable"
            ) from exc
        if run.capability_id != PROJECT_ONBOARDING_CAPABILITY.id:
            raise ConversationTaskInputError("Workflow is not an onboarding task")
        if run.status is not WorkflowRunStatus.INTERRUPTED:
            raise ConversationTaskInputError("Onboarding workflow is not waiting for input")
        if run.version != expected_version:
            raise ConversationTaskConflictError("Onboarding workflow version is stale")
        interrupts = await self._workflow_service.pending_interrupts(run.id, context.user_id)
        if len(interrupts) != 1:
            raise ConversationTaskInputError(
                "Onboarding workflow input is unavailable or ambiguous"
            )
        interrupt = interrupts[0]
        required = {
            str(item)
            for item in (interrupt.scope_snapshot or {}).get("required_fields", [])
            if isinstance(item, str)
        }
        supplied = {
            key for key, value in inputs.model_dump().items() if value is not None
        }
        if required and not required.issubset(supplied):
            raise ConversationTaskInputError(
                "The supplied input does not satisfy the pending request"
            )
        replay = await self._state.start_effect(
            claim_id=claim_id,
            lease_token=token,
            call_key=call_key,
            capability_id=capability.id,
            authority_hint={
                "kind": "workflow_run",
                "workflow_run_id": run.id,
                "project_id": run.project_id,
            },
        )
        if replay is not None:
            return await self._replay_workflow(capability, replay, context)
        arguments = run.runtime_metadata.get("arguments")
        prior = arguments if isinstance(arguments, dict) else {}
        onboarding_request = OnboardingRequest(
            workflow_run_id=run.id,
            project_id=str(run.project_id),
            repo_url=inputs.repo_url or prior.get("repo_url"),
            source_type=inputs.source_type or prior.get("source_type"),
            platform=inputs.platform or prior.get("platform") or "generic",
            installation_id=prior.get("installation_id"),
            upload_receipt=inputs.upload_receipt,
            selected_pack_ids=[
                str(item)
                for item in prior.get("selected_pack_ids", [])
                if isinstance(item, str)
            ],
            plugin_pack_id=prior.get("plugin_pack_id"),
            thread_id=run.runtime_thread_id,
        )
        try:
            resumed = await self._workflow_service.resume(
                run_id=run.id,
                owner_user_id=context.user_id,
                interrupt_id=interrupt.id,
                expected_version=expected_version,
                response=json.dumps(inputs.model_dump(exclude_none=True), sort_keys=True),
            )
        except HarnessWorkflowConflictError as exc:
            await self._state.abort_effect(
                claim_id=claim_id,
                lease_token=token,
                call_key=call_key,
                capability_id=capability.id,
                reason="Onboarding workflow version changed",
            )
            raise ConversationTaskConflictError(
                "Onboarding workflow version changed; reload and retry"
            ) from exc
        # The exact interrupt consumption and started receipt are durable
        # before resumed graph nodes may perform external effects.
        await self._db.commit()
        try:
            result = await self._onboarding.resume(
                onboarding_request,
                response=inputs.model_dump(exclude_none=True),
                executor=self._subordinate_executor(),
                context=_subordinate_context(context, str(run.project_id)),
            )
        except Exception as exc:
            resumed = await self._workflow_service.mark_failed(
                resumed,
                error_message="Project onboarding failed while resuming.",
                runtime_provider_id="langgraph.nexus",
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=resumed,
            )
            await self._db.commit()
            raise ConversationTaskExecutionError(
                "Project onboarding failed while resuming."
            ) from exc
        return await self._finish_onboarding(
            run=resumed,
            result=result,
            capability=capability,
            claim_id=claim_id,
            token=token,
            call_key=call_key,
            project_id=str(run.project_id),
            arguments=onboarding_request.model_dump(mode="json"),
        )

    async def reconcile_documentation_status(
        self,
        *,
        context: CapabilityExecutionContext,
        requested_project_id: str,
        provider_project_id: str,
        external_run_id: str | int | None,
        provider_status: str,
        started_at: str | None,
        completed_at: str | None,
        error_message: str | None,
    ):
        if (
            external_run_id is None
            or context.conversation_id is None
            or provider_project_id != requested_project_id
            or (
                context.project_scope is not None
                and requested_project_id not in context.project_scope
            )
        ):
            return None
        receipt = await self._workflow_repo.get_child_receipt_by_external(
            provider_id="mcp.docs",
            external_run_id=external_run_id,
        )
        if receipt is None:
            return None
        try:
            scoped_run = await self._workflow_repo.get_run(
                context.project_scope,
                receipt.workflow_run_id,
                owner_user_id=context.user_id,
            )
        except Exception:
            return None
        if (
            scoped_run.conversation_id != context.conversation_id
            or scoped_run.project_id != requested_project_id
        ):
            return None
        try:
            ingested = await DocumentationWorkflowCallbackIngestor(
                self._workflow_repo
            ).ingest(
                provider_id="mcp.docs",
                external_run_id=external_run_id,
                idempotency_key=(
                    f"status:{external_run_id}:{provider_status}:"
                    f"{completed_at or started_at or 'unknown'}"
                )[:255],
                provider_status=provider_status,
                provider_started_at=_parse_provider_time(started_at),
                provider_finished_at=_parse_provider_time(completed_at),
                error_message=error_message,
            )
        except ValueError:
            return None
        run = ingested.run
        if run.status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }:
            if (
                context.conversation_turn_claim_id is not None
                and context.conversation_turn_claim_token is not None
            ):
                call_key = f"workflow-terminal:{run.id}:{run.version}"
                replay = await self._state.start_effect(
                    claim_id=context.conversation_turn_claim_id,
                    lease_token=context.conversation_turn_claim_token,
                    call_key=call_key,
                    capability_id="mcp.docs.get_generation_status",
                    authority_hint={
                        "kind": "workflow_run",
                        "workflow_run_id": run.id,
                        "project_id": run.project_id,
                    },
                )
                if replay is None:
                    await self._state.record_effect(
                        claim_id=context.conversation_turn_claim_id,
                        lease_token=context.conversation_turn_claim_token,
                        effect=ConversationTaskEffect(
                            call_key=call_key,
                            capability_id="mcp.docs.get_generation_status",
                            remove_task_ref_id=f"workflow:{run.id}",
                        ),
                        domain_ref={
                            "workflow_run_id": run.id,
                            "project_id": run.project_id,
                            "status": run.status.value,
                        },
                    )
                await self._db.commit()
        return run

    async def _finish_onboarding(
        self,
        *,
        run,
        result: OnboardingResult,
        capability: CapabilityDefinition,
        claim_id: int,
        token: str,
        call_key: str,
        project_id: str,
        arguments: dict[str, Any],
    ) -> CapabilityObservation:
        requested: list[ConversationRequestedInput] = []
        if result.status == "interrupted":
            run = await self._workflow_service.mark_interrupted(
                run,
                output_summary="Project source information is required.",
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=result.thread_id,
                current_node=result.current_node,
                runtime_metadata={
                    "graph": "project_onboarding",
                    "arguments": arguments,
                },
                interrupt_type=WorkflowInterruptType.CLARIFICATION,
                prompt=result.interrupt_prompt or "Which source should Nexus use?",
                options=[],
                scope_snapshot={
                    "project_id": project_id,
                    "required_fields": result.interrupt_fields,
                },
                policy_snapshot={"capability": capability.id},
                normalized_arguments={"project_id": project_id},
            )
        elif result.status == "failed":
            error = next(
                (
                    item.error_message or item.summary
                    for item in reversed(result.observations)
                    if not item.succeeded
                ),
                "Project onboarding could not continue.",
            )
            run = await self._workflow_repo.update_run(
                [project_id],
                run.id,
                WorkflowRunUpdate(
                    status=WorkflowRunStatus.FAILED,
                    error_message=error,
                    runtime_provider_id="langgraph.nexus",
                    runtime_thread_id=result.thread_id,
                    current_node=result.current_node,
                    finished_at=datetime.datetime.now(datetime.UTC),
                ),
                owner_user_id=run.user_id,
            )
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
            await self._db.commit()
            return CapabilityObservation(
                capability_id=capability.id,
                status="failed",
                output_type=capability.output_type,
                summary=error,
                error_message=error,
                workflow_ref={
                    "workflow_run_id": run.id,
                    "project_id": project_id,
                    "status": run.status.value,
                },
            )
        else:
            run = await self._workflow_service.mark_succeeded(
                run,
                output_summary="Project onboarding completed.",
                artifact_links=result.artifact_links,
            )
        task = _workflow_task(
            run.id,
            ConversationTaskKind.ONBOARDING_WORKFLOW,
            PROJECT_ONBOARDING_CAPABILITY.id,
            project_id,
        )
        if result.status == "interrupted":
            requested = [
                ConversationRequestedInput(
                    task_ref_id=task.task_ref_id,
                    field=field,
                    prompt=result.interrupt_prompt or "Provide the requested project source.",
                )
                for field in result.interrupt_fields[:10]
            ]
        if run.status is WorkflowRunStatus.SUCCEEDED:
            await self._record_terminal_workflow(
                capability=capability,
                claim_id=claim_id,
                token=token,
                call_key=call_key,
                run=run,
            )
        else:
            await self._state.record_effect(
                claim_id=claim_id,
                lease_token=token,
                effect=ConversationTaskEffect(
                    call_key=call_key,
                    capability_id=capability.id,
                    task_ref=task,
                    objective=f"Onboard project {project_id}",
                    referenced_entities=[
                        ConversationEntityReference(
                            entity_type="project",
                            stable_id=project_id,
                            display_name=project_id,
                            project_id=project_id,
                        )
                    ],
                    requested_inputs=requested,
                ),
                domain_ref={
                    "workflow_run_id": run.id,
                    "project_id": project_id,
                    "status": run.status.value,
                },
            )
        await self._db.commit()
        return CapabilityObservation(
            capability_id=capability.id,
            status=("succeeded" if run.status is WorkflowRunStatus.SUCCEEDED else "triggered"),
            output_type=capability.output_type,
            summary=(
                result.interrupt_prompt
                if result.status == "interrupted"
                else "Project onboarding completed."
            ),
            data={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "status": run.status.value,
                "requested_inputs": [item.model_dump(mode="json") for item in requested],
            },
            artifact_links=result.artifact_links,
            workflow_ref={
                "workflow_run_id": run.id,
                "project_id": project_id,
                "status": run.status.value,
            },
            task_ref=(None if run.status is WorkflowRunStatus.SUCCEEDED else task),
        )

    async def _record_terminal_workflow(
        self,
        *,
        capability: CapabilityDefinition,
        claim_id: int,
        token: str,
        call_key: str,
        run,
    ) -> None:
        await self._state.record_effect(
            claim_id=claim_id,
            lease_token=token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability.id,
                remove_task_ref_id=f"workflow:{run.id}",
            ),
            domain_ref={
                "workflow_run_id": run.id,
                "project_id": run.project_id,
                "status": run.status.value,
            },
        )

    async def _replay_workflow(
        self,
        capability: CapabilityDefinition,
        receipt: ConversationEffectReceipt,
        context: CapabilityExecutionContext,
    ) -> CapabilityObservation:
        workflow_run_id = receipt.domain_ref.get("workflow_run_id")
        if not isinstance(workflow_run_id, int):
            raise RuntimeError("Workflow receipt is missing its local authority")
        run = await self._workflow_repo.get_run(
            context.project_scope,
            workflow_run_id,
            owner_user_id=context.user_id,
        )
        terminal = run.status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }
        return CapabilityObservation(
            capability_id=capability.id,
            status=("failed" if run.status is WorkflowRunStatus.FAILED else "succeeded" if terminal else "triggered"),
            output_type=capability.output_type,
            summary=run.output_summary or run.error_message or f"Workflow is {run.status.value}.",
            data={
                "workflow_run_id": run.id,
                "project_id": run.project_id,
                "status": run.status.value,
            },
            error_message=(run.error_message if run.status is WorkflowRunStatus.FAILED else None),
            workflow_ref={
                "workflow_run_id": run.id,
                "project_id": run.project_id,
                "status": run.status.value,
            },
            task_ref=(None if terminal else receipt.effect.task_ref),
        )

    def _subordinate_executor(self) -> HarnessCapabilityExecutor:
        return HarnessCapabilityExecutor(
            self._registry,
            docs_provider=self._docs,
            projects_provider=self._projects,
            conversation_state_repository=self._state,
            conversation_task_service=self,
        )


def _claim(
    context: CapabilityExecutionContext, request: CapabilityExecutionRequest
) -> tuple[int, str, str, int]:
    if (
        context.conversation_id is None
        or context.conversation_turn_claim_id is None
        or context.conversation_turn_claim_token is None
        or request.idempotency_key is None
    ):
        raise RuntimeError("Workflow task requires an authoritative turn claim")
    return (
        context.conversation_turn_claim_id,
        context.conversation_turn_claim_token,
        request.idempotency_key,
        context.conversation_id,
    )


def _workflow_task(
    workflow_run_id: int,
    kind: ConversationTaskKind,
    capability_id: str,
    project_id: str,
) -> ConversationTaskReference:
    return ConversationTaskReference(
        task_ref_id=f"workflow:{workflow_run_id}",
        kind=kind,
        capability_id=capability_id,
        workflow_run_id=workflow_run_id,
        project_id=project_id,
        focused=True,
    )


def _subordinate_context(
    context: CapabilityExecutionContext, project_id: str
) -> CapabilityExecutionContext:
    return context.model_copy(
        update={
            "project_scope": list(dict.fromkeys([*(context.project_scope or []), project_id])),
            "approvals": {
                *context.approvals,
                ATTACH_GIT_SOURCE_CAPABILITY.id,
                ATTACH_ZIP_SOURCE_CAPABILITY.id,
            },
        }
    )


def _parse_provider_time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


__all__ = ["ConversationTaskService", "OnboardingResumeInputs"]
