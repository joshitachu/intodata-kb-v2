"""Auditable Slice 5 inventory of preserved child-workflow adapter families."""

from __future__ import annotations

from dataclasses import dataclass

from src.harness.registry import CapabilityRegistry


@dataclass(frozen=True, slots=True)
class WorkflowFamilyAdapter:
    family: str
    capability_ids: tuple[str, ...]
    authority: str
    start: bool
    interrupt: bool
    resume: bool
    restart: bool
    terminal: bool


WORKFLOW_FAMILY_ADAPTERS = (
    WorkflowFamilyAdapter(
        "project_onboarding",
        ("nexus.workflow.onboard_project", "nexus.workflow.resume_onboarding"),
        "harness_workflow_runs",
        True, True, True, True, True,
    ),
    WorkflowFamilyAdapter(
        "pack_builder",
        (
            "nexus.packs.create", "nexus.packs.drafts.create", "nexus.packs.drafts.mutate",
            "nexus.packs.drafts.validate", "nexus.packs.drafts.save",
            "nexus.packs.drafts.abandon",
        ),
        "harness_pack_drafts",
        True, True, True, True, True,
    ),
    WorkflowFamilyAdapter(
        "evidence_investigation",
        ("rag.docs.search_evidence", "runtime.stored.query_evidence", "runtime.logs.search_evidence"),
        "evidence_refs_and_workflow_activity",
        True, False, False, True, True,
    ),
    WorkflowFamilyAdapter(
        "documentation_update",
        (
            "nexus.workflow.propose_documentation_update",
            "mcp.docs.update_from_evidence",
            "mcp.docs.revert_evidence_update",
        ),
        "harness_workflow_runs_and_artifact_receipts",
        True, True, True, True, True,
    ),
    WorkflowFamilyAdapter(
        "generic_durable_capability",
        (
            "nexus.conversation_tasks.background_conversation_task",
            "nexus.conversation_tasks.pause_conversation_task",
            "nexus.conversation_tasks.resume_conversation_task",
        ),
        "conversation_turn_claims_and_effect_operations",
        True, True, True, True, True,
    ),
    WorkflowFamilyAdapter(
        "documentation_generation_external_child",
        (
            "mcp.docs.generate_project_documentation",
            "mcp.docs.get_generation_status",
            "nexus.workflows.cancel",
        ),
        "harness_workflow_runs_and_child_receipts",
        True, True, True, True, True,
    ),
)


def validate_workflow_family_inventory(registry: CapabilityRegistry) -> None:
    families = {item.family for item in WORKFLOW_FAMILY_ADAPTERS}
    if len(families) != len(WORKFLOW_FAMILY_ADAPTERS):
        raise ValueError("Workflow family names must be unique")
    for adapter in WORKFLOW_FAMILY_ADAPTERS:
        if not adapter.capability_ids or not adapter.authority:
            raise ValueError(f"Workflow family {adapter.family} is incomplete")
        for capability_id in adapter.capability_ids:
            registry.get(capability_id)
            registry.handler_for(capability_id)


__all__ = ["WORKFLOW_FAMILY_ADAPTERS", "WorkflowFamilyAdapter", "validate_workflow_family_inventory"]
