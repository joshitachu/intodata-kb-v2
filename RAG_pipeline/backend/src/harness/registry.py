"""Capability discovery and executable-handler registries."""

from __future__ import annotations

from collections.abc import Iterable

from src.harness import CapabilityDisabledError, UnknownCapabilityError
from src.harness.capability_runtime import CapabilityHandler
from src.harness.models import (
    ATTACH_GIT_SOURCE_CAPABILITY,
    ATTACH_ZIP_SOURCE_CAPABILITY,
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    DOCUMENTATION_PACK_LIST_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_ANSWER_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    DOCUMENT_UPDATE_PROPOSAL_CAPABILITY,
    LOG_SEARCH_EVIDENCE_CAPABILITY,
    METADATA_QUERY_CAPABILITY,
    ONBOARDING_RESUME_CAPABILITY,
    PACK_CREATE_CAPABILITY,
    PACK_DRAFT_ABANDON_CAPABILITY,
    PACK_DRAFT_CREATE_CAPABILITY,
    PACK_DRAFT_REFINE_CAPABILITY,
    PACK_DRAFT_READ_CAPABILITY,
    PACK_DRAFT_SAVE_CAPABILITY,
    PACK_DRAFT_VALIDATE_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    PROJECT_STATUS_CAPABILITY,
    PUBLISH_INDEX_CAPABILITY,
    SOURCE_OPTIONS_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    TRIGGER_SCAN_CAPABILITY,
    WORKFLOW_CANCELLATION_CAPABILITY,
    CapabilityDefinition,
    CapabilityHealth,
)


class CapabilityRegistryError(ValueError):
    """Raised when definitions and execution bindings disagree."""


class CapabilityCatalog:
    """Non-executable definitions used for discovery and manifest rendering."""

    def __init__(
        self,
        capabilities: Iterable[CapabilityDefinition] = (),
    ) -> None:
        capability_list = list(capabilities)
        duplicate_ids = _duplicates(item.id for item in capability_list)
        if duplicate_ids:
            raise CapabilityRegistryError(
                "Duplicate capability definition(s): " + ", ".join(duplicate_ids)
            )
        self._capabilities = {
            capability.id: capability for capability in capability_list
        }

    def register(self, capability: CapabilityDefinition) -> None:
        if capability.id in self._capabilities:
            raise CapabilityRegistryError(
                f"Duplicate capability definition: {capability.id}"
            )
        self._capabilities[capability.id] = capability

    def list(self, *, enabled_only: bool = False) -> list[CapabilityDefinition]:
        capabilities = list(self._capabilities.values())
        if enabled_only:
            capabilities = [
                capability
                for capability in capabilities
                if capability.enabled
                and capability.health != CapabilityHealth.UNAVAILABLE
            ]
        return sorted(capabilities, key=lambda capability: capability.id)

    def get(self, capability_id: str) -> CapabilityDefinition:
        capability = self._capabilities.get(capability_id)
        if capability is None:
            raise UnknownCapabilityError(f"Unknown capability: {capability_id}")
        return capability

    def require_available(self, capability_id: str) -> CapabilityDefinition:
        capability = self.get(capability_id)
        if not capability.enabled or capability.health == CapabilityHealth.UNAVAILABLE:
            raise CapabilityDisabledError(
                f"Capability is unavailable: {capability_id}"
            )
        return capability


class CapabilityRegistry(CapabilityCatalog):
    """One executable handler binding for every registered definition."""

    def __init__(
        self,
        capabilities: Iterable[CapabilityDefinition] = (),
        *,
        handlers: Iterable[CapabilityHandler] = (),
    ) -> None:
        super().__init__(capabilities)
        handler_list = list(handlers)
        duplicate_handlers = _duplicates(
            handler.capability_id for handler in handler_list
        )
        if duplicate_handlers:
            raise CapabilityRegistryError(
                "Duplicate capability handler(s): "
                + ", ".join(duplicate_handlers)
            )
        self._handlers = {
            handler.capability_id: handler for handler in handler_list
        }
        capability_ids = set(self._capabilities)
        handler_ids = set(self._handlers)
        missing_handlers = capability_ids - handler_ids
        if missing_handlers:
            raise CapabilityRegistryError(
                "Missing registered handler(s): "
                + ", ".join(sorted(missing_handlers))
            )
        orphan_handlers = handler_ids - capability_ids
        if orphan_handlers:
            raise CapabilityRegistryError(
                "Handler has no capability definition: "
                + ", ".join(sorted(orphan_handlers))
            )
        for capability_id, handler in self._handlers.items():
            self._validate_handler(self._capabilities[capability_id], handler)

    def register(
        self,
        capability: CapabilityDefinition,
        handler: CapabilityHandler,
    ) -> None:
        if capability.id in self._capabilities:
            raise CapabilityRegistryError(
                f"Duplicate capability definition: {capability.id}"
            )
        self._validate_handler(capability, handler)
        self._capabilities[capability.id] = capability
        self._handlers[capability.id] = handler

    def handler_for(self, capability_id: str) -> CapabilityHandler:
        capability = self.require_available(capability_id)
        return self._handlers[capability.id]

    @staticmethod
    def _validate_handler(
        capability: CapabilityDefinition,
        handler: CapabilityHandler,
    ) -> None:
        if handler.capability_id != capability.id:
            raise CapabilityRegistryError(
                f"Handler ID mismatch for {capability.id}: {handler.capability_id}"
            )
        if dict(handler.input_schema) != capability.input_schema:
            raise CapabilityRegistryError(
                f"Handler input schema mismatch for {capability.id}"
            )
        if handler.output_type != capability.output_type:
            raise CapabilityRegistryError(
                f"Handler output contract mismatch for {capability.id}: "
                f"{handler.output_type} != {capability.output_type}"
            )
        if tuple(handler.task_types) != tuple(capability.task_types):
            raise CapabilityRegistryError(
                f"Handler task contract mismatch for {capability.id}"
            )


def base_capability_registry() -> CapabilityRegistry:
    """Return the authoritative executable built-in capability registry."""

    from src.harness.agent_plans import agent_plan_capabilities
    from src.harness.task_controls import conversation_task_control_capabilities
    from src.harness.topology_capability import (
        topology_capabilities,
        topology_capability_handlers,
    )
    capabilities = [
        DOCUMENT_SEARCH_CAPABILITY,
        DOCUMENTATION_ANSWER_CAPABILITY,
        DOCUMENT_READ_CAPABILITY,
        DOCUMENT_LIST_CAPABILITY,
        GENERATED_DOCUMENT_READ_CAPABILITY,
        METADATA_QUERY_CAPABILITY,
        CONVERSATION_SUMMARY_FACTS_CAPABILITY,
        STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
        LOG_SEARCH_EVIDENCE_CAPABILITY,
        DOCUMENTATION_GENERATION_CAPABILITY,
        DOCUMENTATION_PACK_LIST_CAPABILITY,
        DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
        PACK_CREATE_CAPABILITY,
        PACK_DRAFT_CREATE_CAPABILITY,
        PACK_DRAFT_READ_CAPABILITY,
        PACK_DRAFT_REFINE_CAPABILITY,
        PACK_DRAFT_VALIDATE_CAPABILITY,
        PACK_DRAFT_SAVE_CAPABILITY,
        PACK_DRAFT_ABANDON_CAPABILITY,
        DOCUMENT_UPDATE_PROPOSAL_CAPABILITY,
        DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
        DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY,
        PROJECT_ONBOARDING_CAPABILITY,
        ONBOARDING_RESUME_CAPABILITY,
        PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
        WORKFLOW_CANCELLATION_CAPABILITY,
        SOURCE_OPTIONS_CAPABILITY,
        ATTACH_GIT_SOURCE_CAPABILITY,
        ATTACH_ZIP_SOURCE_CAPABILITY,
        TRIGGER_SCAN_CAPABILITY,
        PROJECT_STATUS_CAPABILITY,
        PUBLISH_INDEX_CAPABILITY,
        *agent_plan_capabilities(),
        *conversation_task_control_capabilities(),
        # Topology reads published artifacts rather than a live runtime API, so it
        # is registered unconditionally: it stays available on deployments with the
        # live runtime provider switched off.
        *topology_capabilities(),
    ]
    from src.harness.capability_handlers import migrated_capability_handlers
    from src.harness.agent_plans import AgentPlanCapabilityHandler
    from src.harness.task_controls import ConversationTaskControlHandler

    return CapabilityRegistry(
        capabilities,
        handlers=(*migrated_capability_handlers(), *(AgentPlanCapabilityHandler(item) for item in agent_plan_capabilities()), *(ConversationTaskControlHandler(item) for item in conversation_task_control_capabilities()), *topology_capability_handlers()),
    )


def default_capability_registry() -> CapabilityRegistry:
    from src.harness.packs.runtime import compile_active_pack_registry

    return compile_active_pack_registry(base_capability_registry())


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
